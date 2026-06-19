---
on:
  workflow_dispatch:
    inputs:
      pr_number:   { description: "PR number", required: true }
      repo:        { description: "owner/name (defaults to this repo)", required: false }
      scripts_ref: { description: "custody ref to run analysis scripts from", required: false, default: "main" }
permissions: { contents: read, pull-requests: read, issues: read }
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway below (Tailscale Funnel,
  # reachable from GitHub runners). gh-aw injects OPENAI_API_KEY (repo secret) — same setup as the
  # preflight gate. The agent needs no GitHub network access (PR data is prefetched in steps:).
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
    # codex's `defaults` omits the gateway host.
    - arcyleung-ubuntu.tailb940e6.ts.net
safe-outputs: { staged: true, noop: {} }
tools:
  bash: [ "node:*", "cat:*", "echo:*" ]
  edit:
steps:
  # The target repo must be checked out into the workspace ROOT — gh-aw's agent job runs
  # "Configure Git credentials" before its own PR-branch checkout, so a root .git must exist.
  - name: Checkout (repo workspace for the gh-aw agent + git)
    uses: actions/checkout@v4
    with: { persist-credentials: false }
  # Analysis scripts come from custody (minimal target injection — the target carries only the lock).
  - name: Checkout custody analysis scripts (pinned)
    uses: actions/checkout@v4
    with:
      repository: SiRumCz/custody
      ref: ${{ github.event.inputs.scripts_ref || 'main' }}
      token: ${{ secrets.CUSTODY_SCRIPTS_TOKEN }}
      path: _custody
      persist-credentials: false
      sparse-checkout: |
        app/backend/component/risk
        app/backend/core
  - name: Prefetch (stage the PR + context for the agent)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", GITHUB_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ github.event.inputs.pr_number }}", REPO: "${{ github.event.inputs.repo || github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,author,body,files,baseRefName,headRefName,headRefOid > /tmp/gh-aw/agent/pr.json
      gh api "repos/$REPO/pulls/$PR/files" --paginate > /tmp/gh-aw/agent/pr-files.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || {
        echo "::warning::pr diff too large; assembling per-file patches (may be partial)"
        gh api "repos/$REPO/pulls/$PR/files" --paginate \
          --jq '.[] | "diff --git a/\(.filename) b/\(.filename)\n--- a/\(.filename)\n+++ b/\(.filename)\n\(.patch // "(patch omitted: too large)")\n"' \
          > /tmp/gh-aw/agent/pr.diff
      }
      HEAD_SHA=$(node -e "process.stdout.write(JSON.parse(require('fs').readFileSync('/tmp/gh-aw/agent/pr.json','utf8')).headRefOid||'')")
      node _custody/app/backend/component/risk/workflow/scripts/prefetch-context.js "$REPO" "$PR" "$HEAD_SHA" > /tmp/gh-aw/agent/context.json || echo "{}" > /tmp/gh-aw/agent/context.json
post-steps:
  - name: Assemble → overview.json
    if: always()
    run: node _custody/app/backend/component/risk/workflow/scripts/assemble.js /tmp/gh-aw/agent/pr.json /tmp/gh-aw/agent/pr-files.json /tmp/gh-aw/agent/overview-findings.jsonl > /tmp/gh-aw/overview.json
  - name: Upload overview result
    if: always()
    uses: actions/upload-artifact@v4
    with: { name: overview-triage, path: /tmp/gh-aw/overview.json, retention-days: 7 }
---

# Guided Overview — one cohort partition (walkthrough + breaking-change risk)

You produce a guided, layered walkthrough of a PR AND the breaking-change findings, grouped into a
SINGLE set of change cohorts shared by both. The deterministic risk factors (diffusion, churn) and
the banded score are computed separately downstream — do **not** compute them.

1. Read `/tmp/gh-aw/agent/pr.json` (changed files: `files[].path`, `additions`, `deletions`),
   `/tmp/gh-aw/agent/pr.diff`, and `/tmp/gh-aw/agent/context.json` (project description/README and
   the full contents of changed files at head, when available — read these for context).
2. **Split the change into one or more INDEPENDENT CHANGE COHORTS — groups of related work that can
   each be understood on their own. A small PR may be a single cohort.** Every changed file belongs
   to exactly one cohort. Assign each cohort an `area` for routing, one of:
   `security`, `frontend`, `backend`, `data`, `infra`, `docs`, `tests`.
3. Within each cohort, break the work into LAYERS ordered by dependency / build order, the way a
   thoughtful senior engineer would walk a colleague through it. Typical progression:
   schema → backend → api → frontend → tests. Use `other` for layers that fit none of these.
   For EACH layer record: `layer` (one of schema|backend|api|frontend|tests|other), `order`
   (1-based within the cohort), `area` (same vocabulary as above), `title` (≤8 words),
   `summary` (2-3 sentences, relative to the previous layer), `files` (repo-relative paths exactly
   as in the diff headers), `diff` (≤30 relevant unified-diff lines), and OPTIONAL `diagram`
   (a Mermaid source string; omit the field entirely when not useful).
4. For each cohort, **detect breaking changes** to the PUBLIC API against the **APIDiff taxonomy**,
   language-general via per-language public-symbol cues (Go: exported identifiers; JS/TS: `export`s;
   Python: public names without a leading underscore). Classify each finding's `severityClass`:
   - `hard-break` — REMOVE_TYPE / REMOVE_METHOD / REMOVE_FIELD, LOST_VISIBILITY, CHANGE_IN_RETURN_TYPE,
     CHANGE_IN_PARAMETER_LIST, CHANGE_IN_FIELD_TYPE / SUPERTYPE / EXCEPTION_LIST (signature/semantic-modifying).
   - `recoverable-refactor` — RENAME_*, MOVE_*, PUSH_DOWN_*, INLINE_* (semantic-preserving; a client can adapt mechanically).
   Removing a **deprecated** element is NON-breaking — do not record it. Likewise, replacing or
   implementing a **stub / placeholder** (e.g. a `501 Not Implemented` route, a `NotImplementedError`,
   or a TODO/empty body) with a real implementation is NON-breaking — do not record it. Behavioral-only
   changes that preserve the signature are out of scope.
5. Append **one JSON object per cohort** (one per line) to `/tmp/gh-aw/agent/overview-findings.jsonl`:
   `{"cohort":"…","cohortOrder":1,"area":"backend","files":["…"],"layers":[{"layer":"backend","order":1,"area":"backend","title":"…","summary":"…","files":["…"],"diff":"…","diagram":"…"}],"bcFindings":[{"symbol":"…","kind":"type|method|field","category":"REMOVE_METHOD|…","severityClass":"hard-break|recoverable-refactor","evidence":"…"}]}`
   `cohortOrder` is a 1-based integer ordering the cohorts. A cohort with no public-API change has
   `"bcFindings":[]`.
6. Append **one final summary object** on its own line:
   `{"type":"summary","summary":"one sentence on what this PR does at a high level","diagram":"Mermaid source or null"}`
   Output ONLY newline-delimited JSON — one object per line, no markdown, no preamble. Use the bash
   `echo`/`edit` tools to write the file. Then call `noop`. Never write to the repository.

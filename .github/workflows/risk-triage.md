---
on:
  workflow_dispatch:
    inputs:
      pr_number: { description: "PR number", required: true }
      repo:      { description: "owner/name (defaults to this repo)", required: false }
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
  - name: Checkout (scripts live in this repo)
    uses: actions/checkout@v4
    with: { persist-credentials: false }
  - name: Prefetch (stage the PR for the agent)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ github.event.inputs.pr_number }}", REPO: "${{ github.event.inputs.repo || github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,author,body,files,baseRefName,headRefName,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || {
        echo "::warning::pr diff too large; assembling per-file patches (may be partial)"
        gh api "repos/$REPO/pulls/$PR/files" --paginate \
          --jq '.[] | "diff --git a/\(.filename) b/\(.filename)\n--- a/\(.filename)\n+++ b/\(.filename)\n\(.patch // "(patch omitted: too large)")\n"' \
          > /tmp/gh-aw/agent/pr.diff
      }
post-steps:
  - name: Score → risk.json
    if: always()
    run: node app/backend/component/risk/workflow/scripts/score-run.js /tmp/gh-aw/agent/pr.json /tmp/gh-aw/agent/risk-findings.jsonl > /tmp/gh-aw/risk.json
  - name: Upload risk result
    if: always()
    uses: actions/upload-artifact@v4
    with: { name: risk-triage, path: /tmp/gh-aw/risk.json, retention-days: 7 }
---

# Risk Triage — breaking-change cohorts

You produce the breaking-change findings for a PR, grouped into change cohorts. The deterministic
factors (diffusion, churn) and the banded score are computed separately — do **not** compute them.

1. Read `/tmp/gh-aw/agent/pr.json` (changed files: `files[].path`, `additions`, `deletions`) and
   `/tmp/gh-aw/agent/pr.diff`.
2. **Split the change into one or more INDEPENDENT CHANGE COHORTS — groups of related work that can
   each be understood on their own. A small PR may be a single cohort.** (This is the same cohort
   rubric the review component uses; risk scores per cohort.) Every changed file belongs to exactly
   one cohort. Assign each cohort an `area` for routing, one of:
   `security`, `frontend`, `backend`, `data`, `infra`, `docs`, `tests`.
3. For each cohort, **detect breaking changes** to the PUBLIC API against the **APIDiff taxonomy**,
   language-general via per-language public-symbol cues (Go: exported identifiers; JS/TS: `export`s;
   Python: public names without a leading underscore). Classify each finding's `severityClass`:
   - `hard-break` — REMOVE_TYPE / REMOVE_METHOD / REMOVE_FIELD, LOST_VISIBILITY, CHANGE_IN_RETURN_TYPE,
     CHANGE_IN_PARAMETER_LIST, CHANGE_IN_FIELD_TYPE / SUPERTYPE / EXCEPTION_LIST (signature/semantic-modifying).
   - `recoverable-refactor` — RENAME_*, MOVE_*, PUSH_DOWN_*, INLINE_* (semantic-preserving; a client can adapt mechanically).
   Removing a **deprecated** element is NON-breaking — do not record it. Likewise, replacing or
   implementing a **stub / placeholder** (e.g. a `501 Not Implemented` route, a `NotImplementedError`,
   or a TODO/empty body) with a real implementation is NON-breaking — it adds capability and breaks no
   existing client; do not record it. Behavioral-only changes that preserve the signature are out of scope.
4. Append **one JSON object per cohort** (one per line) to `/tmp/gh-aw/agent/risk-findings.jsonl`:
   `{"cohort":"…","cohortOrder":1,"area":"backend","files":["…"],"bcFindings":[{"symbol":"…","kind":"type|method|field","category":"REMOVE_METHOD|…","severityClass":"hard-break|recoverable-refactor","evidence":"…"}]}`
   `files` are the repo-relative paths exactly as they appear in the diff headers; `cohortOrder` is a
   1-based integer ordering the cohorts. A cohort with no public-API change has `"bcFindings":[]`.
   Output ONLY newline-delimited JSON — one object per line, no markdown, no preamble. Use the bash
   `echo`/`edit` tools to write the file. Then call `noop`. Never write to the repository.

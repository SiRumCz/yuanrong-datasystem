---
name: "Overview Agent Claude (protocol state: overview)"
run-name: "Overview Agent Claude · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
strict: false
sandbox:
  agent: false
engine:
  id: claude
  model: claude-sonnet-4-6
  # Claude via the private Anthropic-compatible gateway (a DIFFERENT host than the
  # codex gateway) — accepts Bearer auth, no token-steering, so we bypass AWF's
  # api-proxy (sandbox.agent: false) and let the claude CLI call it directly.
  # engine.env (not top-level env) is what gh-aw forwards to the CLI subprocess.
  env:
    ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
    ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
permissions:
  contents: read
  pull-requests: read
  issues: read
safe-outputs:
  staged: true
  # threat-detection runs inside the AWF sandbox; with sandbox.agent: false (needed
  # for the direct claude-gateway call) it can't run, so disable it explicitly.
  threat-detection: false
  noop: {}
tools:
  bash: [ "cat:*", "echo:*" ]
  edit:
steps:
  # The repo must be checked out into the workspace ROOT — gh-aw's agent job runs
  # "Configure Git credentials" before its own checkout, so a root .git must exist.
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Prefetch PR (view + diff) for the overview agent
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,body,files,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cat /tmp/gh-aw/task-context.json
post-steps:
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 10
---

# Guided Overview + Risk — cohort partition, layered walkthrough, breaking-change findings

You produce a guided, layered walkthrough of a PR AND its breaking-change findings,
grouped into a SINGLE set of change cohorts shared by both, plus a one-line summary. The
agent does ONLY the AI judgment — partition cohorts, walk the layers, and classify
breaking changes; do not check out the repo, do not run any scripts, and do NOT compute
the risk score. The authoritative risk band is computed deterministically downstream by
the `conclude-overview` hook (a port of score.js/diffusion.js) from your breaking-change
`severityClass` values and cohort file blast radius. You MAY include an optional
`risk_band` HINT, but it is advisory only — the computed band wins.

1. Read `/tmp/gh-aw/agent/pr.json` (changed files: `files[].path`, `additions`, `deletions`;
   plus `title`, `body`, `headRefOid`), `/tmp/gh-aw/agent/pr.diff`, and
   `/tmp/gh-aw/task-context.json` (`pr`, `iteration`, `feedback` — fold any prior `feedback`
   into this pass; `inputs` carries upstream evidence when present).

2. **Split the change into one or more INDEPENDENT CHANGE COHORTS — groups of related work
   that can each be understood on their own. A small PR may be a single cohort.** Every changed
   file belongs to exactly one cohort. Assign each cohort an `area` for routing, one of:
   `security`, `frontend`, `backend`, `data`, `infra`, `docs`, `tests`.

3. Within each cohort, break the work into LAYERS ordered by build dependency, the way a senior
   engineer would walk a colleague through it. Typical progression:
   schema → backend → api → frontend → tests. Use `other` for layers fitting none of these.
   For EACH layer record: `layer` (one of schema|backend|api|frontend|tests|other), `order`
   (1-based within the cohort), `area` (same vocabulary as above), `title` (≤8 words),
   `summary` (2-3 sentences, relative to the previous layer), `files` (repo-relative paths
   exactly as in the diff headers), `diff` (≤30 relevant unified-diff lines), and OPTIONAL
   `diagram` (a Mermaid source string; omit the field entirely when not useful).

4. For each cohort, **detect breaking changes** to the PUBLIC API against the **APIDiff taxonomy**,
   language-general via per-language public-symbol cues (Go: exported identifiers; JS/TS: `export`s;
   Python: public names without a leading underscore). Classify each finding's `severityClass`:
   - `hard-break` — REMOVE_TYPE / REMOVE_METHOD / REMOVE_FIELD, LOST_VISIBILITY,
     CHANGE_IN_RETURN_TYPE, CHANGE_IN_PARAMETER_LIST, CHANGE_IN_FIELD_TYPE / SUPERTYPE /
     EXCEPTION_LIST (signature/semantic-modifying).
   - `recoverable-refactor` — RENAME_* / MOVE_* / PUSH_DOWN_* / INLINE_* (semantic-preserving;
     a client can adapt mechanically).
   Removing a **deprecated** element is NON-breaking — do not record it. Likewise, replacing or
   implementing a **stub / placeholder** (a `501 Not Implemented` route, a `NotImplementedError`,
   a TODO/empty body) with a real implementation is NON-breaking — do not record it. Behavioral-only
   changes that preserve the signature are out of scope. A cohort with no public-API change has
   `"bcFindings":[]`.

5. OPTIONALLY include `risk_band`, one of `Low|Medium|High|Critical`, as an ADVISORY HINT
   only. The authoritative band is computed downstream by `conclude-overview` from your
   `bcFindings` severityClass + cohort file blast radius (score.js/diffusion.js) — do not
   agonize over it, and never treat it as the verdict. You may omit the field entirely.
   What matters most is accurate `bcFindings` (severityClass) and a complete cohort partition.

6. Write `/tmp/gh-aw/evidence.json` (the engine evidence path) as ONE JSON object, using the
   `edit` tool:
   `{"cohorts":[{"cohort":"…","cohortOrder":1,"area":"backend","files":["…"],"layers":[{"layer":"backend","order":1,"area":"backend","title":"…","summary":"…","files":["…"],"diff":"…","diagram":"…"}],"bcFindings":[{"symbol":"…","kind":"type|method|field","category":"REMOVE_METHOD|…","severityClass":"hard-break|recoverable-refactor","evidence":"…"}]}],"summary":"one sentence on what this PR does at a high level","risk_band":"Low|Medium|High|Critical"}`
   `cohortOrder` is a 1-based integer ordering the cohorts. `risk_band` is OPTIONAL (an
   advisory hint; the authoritative band is computed downstream). Write nothing else, then call `noop`.
   Never post comments, never use other safe-outputs, never write to the repository.
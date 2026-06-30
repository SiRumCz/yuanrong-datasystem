---
name: "Fix Agent (protocol state: fix)"
run-name: "Fix Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway below
  # (Tailscale Funnel, reachable from GitHub runners). gh-aw injects OPENAI_API_KEY
  # (repo secret). The agent needs no GitHub network access — PR data is prefetched
  # in steps: (outside the agent firewall); triage evidence arrives inline via aw_context.inputs.
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
    # codex's `defaults` omits the gateway host.
    - arcyleung-ubuntu.tailb940e6.ts.net
permissions:
  contents: read
  pull-requests: read
  issues: read
safe-outputs:
  staged: true
  noop: {}
tools:
  bash: [ "cat:*", "echo:*" ]
  edit:
steps:
  # The repo must be checked out into the workspace ROOT — gh-aw's agent job runs
  # "Configure Git credentials" before its own checkout, so a root .git must exist.
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Prefetch PR + diff
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
source: golivax/agentic-protocol-poc/.github/workflows/fix-agent.md@3c0934933674436397ee54e4847c3f3b990bde95
---

# Fix Agent — propose remediations (suggest mode)

You propose concrete fixes for the findings the upstream **triage** phase already
collected. You act ONLY on triage's clusters — you do not re-review the code or
invent new findings. You emit fix *suggestions* as evidence; you do NOT push
branches, open PRs, or post review comments (the engine/publish does any
world-affecting action later).

## Inputs (already gathered for you)

- `/tmp/gh-aw/task-context.json` — the task context. Read:
  - `.pr` — the PR number.
  - `.iteration`, `.feedback` — if `.iteration` > 1, fold the prior `.feedback`
    into this pass (improve/repair the fixes that failed a check).
  - `.inputs.triage` — the upstream **triage evidence** object: `{ clusters, summary }`.
    Each cluster is `{ cluster_id, title, dimension[], severity, paths[],
    member_findings[], rank }`. This is your fix-queue; it is already inline —
    do NOT fetch it from the network.
- `/tmp/gh-aw/agent/pr.json` — PR metadata (number, title, body, files, headRefOid).
- `/tmp/gh-aw/agent/pr.diff` — the unified PR diff (read the exact current line
  content here when crafting a `suggested_patch`).

Read `/tmp/gh-aw/task-context.json` first, then `/tmp/gh-aw/agent/pr.diff`. Do not
attempt network access.

## Step 1 — guard

If `.inputs.triage` is absent, or `.inputs.triage.clusters` is empty, write
evidence with an empty `fixes` list (see Step 3) — there is nothing to fix — then
call `noop` and stop.

## Step 2 — select clusters

From `triage.clusters`, keep only **code-fixable** clusters — those whose
`dimension` includes at least one of `correctness`, `security`, `performance`,
`maintainability`. **Exclude** any cluster whose only dimension is `test`
(writing tests is out of scope here).

## Step 3 — craft fixes (suggest mode)

For each selected cluster, derive a concrete, minimal change from its
`member_findings` + the diff. Choose a representative member finding with a
concrete `path` and `line` to anchor the suggestion. Only emit a fix when you are
confident of a correct, minimal, single-line / small contiguous edit; if a cluster
needs structural or cross-file changes, **skip** it and record the cluster under
`skipped[]` with a short reason. A wrong fix is worse than a skip — prefer fewer,
correct fixes.

For each fixable cluster, build one `fixes` entry:
- `cluster_id`: the cluster's `cluster_id`.
- `path`: the representative member finding's `path`.
- `line`: the representative member finding's `line` (the anchor in the PR diff, RIGHT side).
- `rationale`: one line — what you change and why (grounded in the cluster + diff).
- `suggested_patch`: the exact replacement line(s) with your change applied. Read
  the current line content from `/tmp/gh-aw/agent/pr.diff` so the patch reproduces
  the target line(s) faithfully, with only the fix applied.

For each selected code-fixable cluster you intentionally do not fix, build one
`skipped` entry:
- `cluster_id`: the cluster's `cluster_id`.
- `reason`: one line explaining why no safe suggestion is emitted.

## Step 4 — write evidence (always)

Write `/tmp/gh-aw/evidence.json` (the engine evidence path) as ONE JSON object,
using the `edit` tool:

`{"fixes":[{"cluster_id":"c1","path":"…","line":1,"rationale":"…","suggested_patch":"…"}], "skipped":[{"cluster_id":"c2","reason":"…"}], "mode":"suggest"}`

`mode` is always `"suggest"` in this phase. Include one `fixes` entry per cluster
you confidently fixed; include one `skipped` entry per code-fixable cluster you
could not safely fix. Write nothing else, then call `noop`.

## Guardrails

- Act only on clusters present in `.inputs.triage.clusters`; never invent findings
  or touch unrelated code.
- Make minimal edits that address the finding; do not reformat surrounding code.
- `mode` is fixed to `suggest`; do NOT push, open PRs, or post comments.

---
name: "Fix Agent (protocol state: fix)"
run-name: "Fix Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
concurrency:
  # Per-dispatch (cid) group so per-issue legs for DIFFERENT issues run in PARALLEL.
  # gh-aw's default `gh-aw-${{ github.workflow }}` shares ONE group across every leg of
  # this workflow; GitHub then keeps only 1 running + 1 pending and cancels the rest
  # while pending, and the engine never re-dispatches a cancelled leg -> the per-issue
  # join deadlocks. cid is unique per leg dispatch. Mirrors impl-feature-auto-*-agent.
  group: "fix-agent-${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}"
  cancel-in-progress: false
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
  bash: [ "cat:*", "echo:*", "python3:*", "pytest:*", "git:*" ]
  edit:
steps:
  # The repo must be checked out into the workspace ROOT — gh-aw's agent job runs
  # "Configure Git credentials" before its own checkout, so a root .git must exist.
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Checkout PR head
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", REPO: "${{ github.repository }}", SHA: "${{ fromJSON(github.event.inputs.aw_context || '{}').sha }}" }
    run: |
      set -euo pipefail
      # The engine dispatches agents ref-lessly, so the checkout above is the default
      # branch — check out the PR head here so the agent edits and tests the real PR code.
      if [ -n "${SHA:-}" ]; then
        git fetch --depth=1 "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" "$SHA"
        git checkout -q "$SHA"
      else
        echo "::warning::no PR head sha in aw_context; editing/testing against the default branch"
      fi
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
source: golivax/agentic-protocol-poc/.github/workflows/fix-agent.md@c6ecf5dad176860d8088573b8be7f5e65e21e3dc
---

# Fix Agent — edit and test (realistic)

You fix the findings the upstream **triage** phase already collected: for each
in-scope finding, fix it by editing the code, then test your change before you
finish — like a real coding agent, not a patch-suggestion bot. You act ONLY on
triage's clusters — you do not re-review the code or invent new findings. You
do NOT push branches, open PRs, or post review comments (the engine/publish
does any world-affecting action later); your evidence is the `git diff` of
what you changed for each cluster.

## Inputs (already gathered for you)

- `/tmp/gh-aw/task-context.json` — the task context. Read:
  - `.pr` — the PR number.
  - `.iteration`, `.feedback` — if `.iteration` > 1, fold the prior `.feedback`
    into this pass (improve/repair the fixes that failed a check).
  - `.inputs.triage` — the upstream **triage evidence** object: `{ clusters, summary }`.
    Each cluster is `{ cluster_id, title, dimension[], severity, paths[],
    member_findings[], rank }`. This is your fix-queue; it is already inline —
    do NOT fetch it from the network.
  - `.inputs.triage.pinned_issue` — the number of the [ai-review] issue this leg
    is fixing (set by fix-triage). Carry it through unchanged into your evidence
    as top-level `pinned_issue` so the honesty legs verify THIS issue.
- `/tmp/gh-aw/agent/pr.json` — PR metadata (number, title, body, files, headRefOid).
- `/tmp/gh-aw/agent/pr.diff` — the unified PR diff, for context on what the PR changed.
- The working tree is already checked out at the PR head (a pre-step did this) —
  edit and test the real files, not a copy.

Read `/tmp/gh-aw/task-context.json` first, then `/tmp/gh-aw/agent/pr.diff`. Do not
attempt network access.

## Step 1 — guard

If `.inputs.triage` is absent, or `.inputs.triage.clusters` is empty, write
evidence with an empty `fixes` list (see Step 4) — there is nothing to fix — then
call `noop` and stop.

## Step 2 — select clusters

From `triage.clusters`, keep only **code-fixable** clusters — those whose
`dimension` includes at least one of `correctness`, `security`, `performance`,
`maintainability`. **Exclude** any cluster whose only dimension is `test`
(writing tests is out of scope here).

## Step 3 — fix each cluster: edit, then test

For each selected cluster:

1. Read its `member_findings` + `/tmp/gh-aw/agent/pr.diff` to pin down the
   defect and the file(s) it lives in.
2. **Edit the file(s) directly** with the `edit` tool to fix the finding.
   Keep the change minimal and focused on the finding — but it may be any
   size the fix genuinely requires: one line, several lines, or across
   multiple files. Do not reformat or touch unrelated code. If a cluster
   needs a change you are not confident is correct and minimal, **skip** it
   instead (Step 3b) — a wrong fix is worse than a skip.
3. **Run the tests that cover your change** using `bash` (`pytest`/`python3`
   are available). You choose which existing test(s) to run — nothing is
   prescribed for you; pick whatever in the repo actually exercises the code
   you touched. If the tests fail, keep iterating (fix, re-run) until they
   pass or you decide to skip the cluster instead.
4. Once the tests pass, capture the unified diff of your edits for this
   cluster with `git diff` (via `bash`), scoped to the file(s) you changed for
   this cluster (e.g. `git diff -- <path...>`) — this is your fix evidence
   for the cluster.

### Step 3b — skip instead of guessing

For each selected code-fixable cluster you intentionally do not fix, build one
`skipped` entry:
- `cluster_id`: the cluster's `cluster_id`.
- `reason`: one line explaining why no safe fix is made.

## Step 4 — write evidence (always)

Write `/tmp/gh-aw/evidence.json` (the engine evidence path) as ONE JSON object.
Because a raw `git diff` contains quotes, backslashes, and newlines, build this
file with a small `python3` snippet (via `bash`) that `json.dumps`s each diff
string rather than hand-typing the diff text with the `edit` tool — hand-typed
escaping is error-prone and will corrupt the diff.

Shape:

`{"fixes":[{"cluster_id":"c1","diff":"diff --git a/... (unified diff of your edits for c1)"}], "skipped":[{"cluster_id":"c2","reason":"…"}], "mode":"edit", "pinned_issue": <the .inputs.triage.pinned_issue integer, omit only when triage had no clusters>}`

`mode` is always `"edit"` in this phase. Include one `fixes` entry per cluster
you edited and tested, with `diff` set to that cluster's unified `git diff`.
Include one `skipped` entry per code-fixable cluster you could not safely fix.
Write nothing else, then call `noop`.

## Guardrails

- Act only on clusters present in `.inputs.triage.clusters`; never invent findings
  or touch unrelated code.
- Make minimal edits that address the finding; do not reformat surrounding code.
- Always run the tests that cover your change before capturing the diff — do
  not report a fix you have not tested.
- `mode` is fixed to `edit`; do NOT push, open PRs, or post comments — your
  edits and test runs stay local to this run; the diff is the evidence.

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
imports:
  # The codex live-guard apparatus: replaces the provisioning engine.command
  # removes (node / codex CLI / AWF), writes the wrapper that registers the
  # PreToolUse hook, and folds the guard's records into evidence.
  - shared/live-guard-codex.md
engine:
  id: codex
  # gh-aw's default 0.135.0 dispatches NO hooks, silently.
  version: "0.147.0"
  model: gpt-5.5
  command: /tmp/gh-aw/cedar-live-guard/codex-with-guard
  # On a fresh runner every hook is untrusted and skipped without warning.
  args: ["--dangerously-bypass-hook-trust"]
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
  - name: Stage plan tool registry (grounds plan_ast in the predefined tool set)
    run: |
      mkdir -p /tmp/gh-aw/agent
      python3 .github/agent-factory/protocols/code-review/scripts/security/plan-tools-catalog.py > /tmp/gh-aw/agent/plan-tools.md 2>/dev/null || true
  - name: Checkout PR head
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", REPO: "${{ github.repository }}", SHA: "${{ fromJSON(github.event.inputs.aw_context || '{}').sha }}" }
    run: |
      set -euo pipefail
      # The engine dispatches agents ref-lessly, so the checkout above is the default
      # branch — check out the PR head here so the agent edits and tests the real PR code.
      if [ -n "${SHA:-}" ]; then
        BASE="$(git rev-parse HEAD)"
        git fetch --depth=1 "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" "$SHA"
        git checkout -q "$SHA"
        # Relocating the workspace takes the protocol tree with it. The live guard
        # provisions from the workspace in `pre-agent-steps`, which run strictly AFTER
        # this step, so a PR branch cut before the engine was installed leaves the guard
        # with nothing to install and the whole leg dies. This is the ONLY guarded agent
        # that moves the tree, which is why waves 1-3 never saw it. Live proof: all four
        # `fix` legs failed both iterations at
        # `::error::live-guard not found at .../scripts/security` (PR 215) — with
        # `live-guard-clean` at `on_fail: "iterate"` that burns the iteration budget on a
        # provisioning fault, not on anything the agent did.
        #
        # Restore ONLY the guard's own directory, and ONLY when the head lacks it. Both
        # halves are load-bearing: files the head does not track cannot appear in the
        # `git diff` the agent captures as its fix evidence, and a PR that genuinely
        # edits the engine keeps its own version rather than being silently overwritten
        # by the default branch's.
        SEC=.github/agent-factory/protocols/code-review/scripts/security
        if [ ! -x "$SEC/live-guard/hook.py" ] || [ ! -d "$SEC/policy/cedar/live" ]; then
          if git rev-parse -q --verify "$BASE:$SEC" >/dev/null; then
            git checkout -q "$BASE" -- "$SEC"
            # Unstage: `git checkout <sha> -- <path>` also writes the index, and a staged
            # path is a tracked path. Leaving it untracked is what keeps it out of the diff.
            git reset -q -- "$SEC"
          else
            echo "::warning::no $SEC on the default branch either; the live guard will fail closed"
          fi
        fi
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
  - name: Set up Python 3.11 for Guardians
    uses: actions/setup-python@v5
    with: { python-version: '3.11' }
  - name: Guardians verify plan_ast (advisory, fail-open)
    if: always()
    run: |
      python3.11 -m pip install --quiet "git+https://github.com/metareflection/guardians@main" z3-solver pydantic pyyaml || true
      SEC=.github/agent-factory/protocols/code-review/scripts/security
      python3.11 "$SEC/verify-plan-ast.py" /tmp/gh-aw/evidence.json "$SEC/policy/guardians/default.policy.yaml" || true
      python3.11 "$SEC/verify-plan-cert.py" /tmp/gh-aw/evidence.json || true
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 10
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

## Plan first, then act — emit your security bundle (`agent_security`)

**Write your plan BEFORE you perform any of the task work above, then carry out the task by
following that plan.** The plan is a commitment you execute against, not a description you
write afterward. Wrap all plan-safety artifacts in ONE `agent_security` object in the JSON
you write to `/tmp/gh-aw/evidence.json` (additive — keep every other evidence field you
already emit):

```json
{
  "agent_security": {
    "plan_kts": "fun plan(...) { ... }",
    "plan_ast": { "steps": [ { "tool": "read_repo_file", "args": { "path": {"$ref":"pr"} }, "result": "diff" } ] },
    "cert":     { "paths": [ ["diff","diff"], ["diff","findings"], ["diff","verdict"] ] }
  }
}
```

FIRST run `cat /tmp/gh-aw/agent/plan-tools.md` — it is the **tool registry**: the fixed,
predefined set of tools your plan is allowed to use. Name every plan step from it; do not
invent tools.

- **`plan_kts`** — your plan as a single Kotlin `.kts` (a readable form): each capability a
  named function call, data through named `val` bindings, any sink destination a string
  literal. Use natural names here.
- **`plan_ast`** — the SAME plan as a restricted workflow AST, **grounded in the registry**:
  one step per tool call, and each step's `"tool"` MUST be EXACTLY one canonical tool from
  `plan-tools.md` (`read_repo_file`/`read_secret`/`read_external`/`write_file`/`run_command`/
  `network_send`/`publish`/`compute`) — never a made-up name. Use `compute` for any pure
  reasoning/analysis step (it does no I/O). Use the arg names the registry shows (e.g.
  `write_file` → `{path, content}`, `network_send`/`publish` → `{host|channel, body}`).
  `args` maps each param to a literal or `{"$ref":"<prior-result>"}`; `result` names the output.
- **`cert`** — your safety certificate: the reachability set over `plan_ast`. For every
  SOURCE step (a read of external/sensitive data), list every variable its data reaches by
  following the `$ref → result` flows, including the source reaching itself. Each entry is
  `[source-variable, reached-variable]`.

**Fidelity contract — `plan_ast` is the manifest of what you actually do.** Then carry out
the task using ONLY the actions your plan declares:
- Every real action you take — reading a file, running a command, writing the evidence, any
  network or publish — MUST correspond to a step in `plan_ast`, at the registry's abstraction
  level (e.g. `cat` an input file → a `read_repo_file` step; your AI judgment/analysis → a
  `compute` step; writing `evidence.json` → a `write_file` step).
- Do NOT take any action your plan doesn't declare, and do NOT declare a step you won't
  perform. If mid-task you find you need an action the plan omits, UPDATE `plan_ast` (and
  `cert`) to include it BEFORE taking it — keep the plan and your execution in lockstep.

A deterministic checker re-derives the facts from `plan_ast` and verifies `cert` (it
rejects fabricated or omitted paths, so you cannot hide a leak). The engine records the
results back under `agent_security` as `guardians`, `cert_verify`, and `cedar`. Purely additive;
nothing gates on it.

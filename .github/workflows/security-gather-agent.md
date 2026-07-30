---
name: "Preflight: security-gather (protocol state: preflight.security-gather)"
run-name: "Security Gather · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway.
  # gh-aw injects OPENAI_API_KEY (repo secret). The noop call below means the
  # agent produces no LLM output — the evidence is assembled deterministically.
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
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
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Stage plan tool registry (grounds plan_ast in the predefined tool set)
    run: |
      mkdir -p /tmp/gh-aw/agent
      python3 .github/agent-factory/protocols/code-review/scripts/security/plan-tools-catalog.py > /tmp/gh-aw/agent/plan-tools.md 2>/dev/null || true
  - name: Prefetch PR metadata
    env:
      GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
      PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}"
      REPO: "${{ github.repository }}"
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" \
        --json number,title,author,body,files,baseRefName,headRefName,headRefOid \
        > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
  - name: Set up Python 3.11 for Guardians
    uses: actions/setup-python@v5
    with:
      python-version: '3.11'
  - name: Run Cedar + Guardians security engines (deterministic, fail-open)
    env:
      GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
      REPO: "${{ github.repository }}"
    run: |
      # Two off-the-shelf engines audit the change for security data-flow risks. Every line is
      # fail-open (|| true / fallback JSON): a missing transcript/plan/dep never fails the run.
      SEC=.github/agent-factory/protocols/code-review/scripts/security
      CTX=.github/agent-factory/protocols/code-review/scripts/context
      A=/tmp/gh-aw/agent
      HEAD_SHA=$(jq -r '.headRefOid // ""' "$A/pr.json" 2>/dev/null || echo "")
      # Captured agent transcript(s) via the protocol's own locator → Cedar input. Read from the
      # dedicated `conversations` branch at <owner>/<repo>/pr-<N>/*.jsonl (not the PR's own tree).
      PRNUM=$(jq -r '.number // empty' "$A/pr.json" 2>/dev/null || echo "")
      CONVERSATIONS_REF=conversations CONVERSATIONS_DIR="$REPO/pr-$PRNUM" \
        node "$CTX/locate.js" "$A/pr.json" "$A/transcripts" || true
      # Plan text: derive the plan path from changed files, fetch at head → Guardians input.
      PLAN_PATH=$(jq -r '[.files[].path] | map(select(test("(?i)(docs/.*plans?/|^plans?/|PLAN\\.md$)"))) | .[0] // ""' "$A/pr.json" 2>/dev/null || echo "")
      if [ -n "$PLAN_PATH" ]; then gh api "repos/$REPO/contents/$PLAN_PATH?ref=$HEAD_SHA" --jq '.content' 2>/dev/null | base64 -d > "$A/plan.txt" || true; fi
      [ -s "$A/plan.txt" ] || : > "$A/plan.txt"
      # Optional per-repo DECLARATIVE custom policy (data only — never executed). LOCKED rules win.
      CCDIR="$A/custom-cedar"; mkdir -p "$CCDIR"
      for fn in $(gh api "repos/$REPO/contents/.custody/policy/cedar?ref=$HEAD_SHA" --jq '.[].name' 2>/dev/null || true); do
        case "$fn" in *.cedar) gh api "repos/$REPO/contents/.custody/policy/cedar/$fn?ref=$HEAD_SHA" --jq '.content' 2>/dev/null | base64 -d > "$CCDIR/$fn" || true ;; esac
      done
      CUSTOM_CEDAR=""; [ -n "$(ls -A "$CCDIR" 2>/dev/null)" ] && CUSTOM_CEDAR="$CCDIR"
      CUSTOM_GUARD=""
      if gh api "repos/$REPO/contents/.custody/policy/guardians.policy.yaml?ref=$HEAD_SHA" --jq '.content' 2>/dev/null | base64 -d > "$A/custom-guardians.yaml" 2>/dev/null; then CUSTOM_GUARD="$A/custom-guardians.yaml"; fi
      # Install the engines (fail-open).
      ( cd "$SEC" && npm install --no-audit --no-fund --silent ) || true
      python3.11 -m pip install --quiet "git+https://github.com/metareflection/guardians@main" z3-solver pydantic pyyaml || true
      # Run: Cedar over the transcript; plan → AST → Guardians; transcript → AST → Guardians; fuse.
      CHANGED=$(jq -c '[.files[].path] // []' "$A/pr.json" 2>/dev/null || echo '[]')
      node "$SEC/run-cedar.js" "$SEC/policy/cedar/default" "$CUSTOM_CEDAR" "$A/transcripts" "$CHANGED" > "$A/cedar.json" 2>/dev/null || echo '{"status":"n/a","flags":[]}' > "$A/cedar.json"
      # Guardians over the PLAN (declared intent).
      node "$SEC/plan-extract.js" "$A/plan.txt" > "$A/gx-workflow.json" 2>/dev/null || echo '{"steps":[]}' > "$A/gx-workflow.json"
      python3.11 "$SEC/verify_driver.py" "$A/gx-workflow.json" "$SEC/policy/guardians/default.policy.yaml" ${CUSTOM_GUARD:+"$CUSTOM_GUARD"} > "$A/guardians.json" 2>/dev/null || echo '{"ok":true,"violations":[],"warnings":[]}' > "$A/guardians.json"
      # Guardians over the TRANSCRIPT (what the agent actually did — real tool calls → same taint AST).
      node "$SEC/transcript-extract.js" "$A/transcripts" > "$A/gx-transcript.json" 2>/dev/null || echo '{"steps":[]}' > "$A/gx-transcript.json"
      python3.11 "$SEC/verify_driver.py" "$A/gx-transcript.json" "$SEC/policy/guardians/default.policy.yaml" ${CUSTOM_GUARD:+"$CUSTOM_GUARD"} > "$A/guardians-transcript.json" 2>/dev/null || echo '{"ok":true,"violations":[],"warnings":[]}' > "$A/guardians-transcript.json"
      node "$SEC/emit-engine-report.js" "$A/cedar.json" "$A/guardians.json" "$A/guardians-transcript.json" > "$A/engine-report.json" 2>/dev/null || echo '{"violations":[],"summary":{}}' > "$A/engine-report.json"
      echo "engine-report:"; cat "$A/engine-report.json" 2>/dev/null || true
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
      ( cd "$SEC" && npm install --no-audit --no-fund --silent ) || true
      python3.11 "$SEC/verify-plan-cedar.py" /tmp/gh-aw/evidence.json "$SEC/policy/cedar/default" || true
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 10
source: golivax/agentic-protocol-poc/.github/workflows/security-gather-agent.md@b99138c649a12218fb6020303a6dec371f244e31
---

# Security Gather — deterministic Cedar + Guardians engine run

You are the **security-gather** preflight step. Your job is to assemble the
deterministic engine evidence object, then call `noop`. You do NOT perform
code review — that is the separate `security-judge` step.

The deterministic engines (Cedar + Guardians) have already run in `steps:` and
their outputs are on disk.

**Your only task:** write the evidence object and call `noop`.

## Inputs (already fetched for you)

- `/tmp/gh-aw/agent/engine-report.json` — fused Cedar + Guardians report. Guardians findings carry a
  `source`: `plan` (declared intent) or `transcript` (what the agent actually did; `name` suffixed
  `@transcript`).
- `/tmp/gh-aw/agent/cedar.json` — raw Cedar output.
- `/tmp/gh-aw/agent/guardians.json` — raw Guardians output over the PLAN.
- `/tmp/gh-aw/agent/guardians-transcript.json` — raw Guardians output over the TRANSCRIPT.
- `/tmp/gh-aw/agent/pr.json` — PR metadata.
- `/tmp/gh-aw/task-context.json` — `pr`, `cid`, `iteration`, `feedback`.

Read these with `cat`. Do not attempt network access.

## Evidence output (required)

Write `/tmp/gh-aw/evidence.json` using the `edit` tool — ONE JSON object:

```json
{
  "scope": {},
  "cedar": <contents of /tmp/gh-aw/agent/cedar.json>,
  "guardians": <contents of /tmp/gh-aw/agent/guardians.json>,
  "engine_report": <contents of /tmp/gh-aw/agent/engine-report.json>,
  "verdict": "PASS" | "LOCKED_VIOLATION" | "n/a",
  "examined": ["<policy ids / files checked by the engines>"]
}
```

**Deterministic verdict rule (NOT a judgment — compute it mechanically):**
- Read `engine_report.violations` (an array).
- If any entry has `"locked": true` → `verdict = "LOCKED_VIOLATION"`.
- If `engine_report.violations` does not exist or both engines produced only
  fallback stubs (no transcript AND no plan) → `verdict = "n/a"` (fail-open —
  NEVER silently set `PASS` when engines could not run).
- Otherwise → `verdict = "PASS"`.

`scope` is always `{}` — security-gather has no scope flags.

`examined` should list the policy ids and/or files the engines checked
(e.g. `["policy/cedar/default", "policy/guardians/default.policy.yaml"]`);
use `[]` only if the engines produced no output at all.

Write nothing else, then call `noop`. Do NOT post comments.

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

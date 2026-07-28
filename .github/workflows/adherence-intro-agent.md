---
name: "Adherence Intro (protocol state: preflight.adherence.adherence-intro)"
run-name: "Adherence Intro · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
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
source: golivax/agentic-protocol-poc/.github/workflows/adherence-intro-agent.md@b99138c649a12218fb6020303a6dec371f244e31
---

# Adherence Intro — cluster entry point (structural glue only)

You are the entry point for the `adherence` cluster sub-pipeline. You do NO analysis.
The real work is done by the inner fanout legs (`spec-solves-issue`, `plan-implements-spec`,
`code-implements-plan`) that follow this step. You exist solely because the engine
requires a dispatchable agent at the branch entry before a nested fanout.

## Task

Write exactly this object to `/tmp/gh-aw/evidence.json`:

```json
{
  "cluster": "adherence",
  "examined": ["cluster entry — fans out to the cluster's legs"]
}
```

Then call `noop`. Do NOT post comments, do NOT read the diff, do NOT do any analysis.
Write nothing else.

## Emit your security bundle (`agent_security`)

Wrap all plan-safety artifacts in ONE `agent_security` object in the JSON you write to
`/tmp/gh-aw/evidence.json` (additive — keep every other evidence field you already emit):

```json
{
  "agent_security": {
    "plan_kts": "fun plan(...) { ... }",
    "plan_ast": { "steps": [ { "tool": "readDiff", "args": { "pr": {"$ref":"pr"} }, "result": "diff" } ] },
    "cert":     { "paths": [ ["diff","diff"], ["diff","findings"], ["diff","verdict"] ] }
  }
}
```

- **`plan_kts`** — your plan as a single Kotlin `.kts`: each capability a named function
  call (`readIssue`/`readDiff`/`readFile`/`analyze`/`writeFile`/`curlPost`…), data through
  named `val` bindings, and any sink destination (`url=`/`path=`) a string literal.
- **`plan_ast`** — the SAME plan as a restricted workflow AST: one step per tool call;
  `args` maps each param to a literal or `{"$ref":"<prior-result>"}`; `result` names the output.
- **`cert`** — your safety certificate: the reachability set over `plan_ast`. For every
  SOURCE step (a read of external/sensitive data), list every variable its data reaches by
  following the `$ref → result` flows, including the source reaching itself. Each entry is
  `[source-variable, reached-variable]`.

A deterministic checker re-derives the facts from `plan_ast` and verifies `cert` (it
rejects fabricated or omitted paths, so you cannot hide a leak). The engine records the
results back under `agent_security` as `guardians`, `cert_verify`, and `cedar`. Purely additive;
nothing gates on it.

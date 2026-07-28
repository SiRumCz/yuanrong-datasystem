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


## Also emit a Kotlin plan of your work (`plan_kts`)

In addition to every field your evidence already requires above, add ONE more field,
**`plan_kts`**, to the JSON object you write to `/tmp/gh-aw/evidence.json`.

Its value is a single Kotlin `.kts` script capturing THIS task's plan — the tool calls
you performed (or would perform) to produce your result — following these rules:
- each capability is a named function call (e.g. `readIssue`, `readSpec`, `readPlan`,
  `readDiff`, `readFile`, `search`, `writeFile`, `curlPost`);
- data flows through named `val` bindings (a step's inputs are prior `val`s or literals;
  its output is a new `val`);
- any sink destination (`url = "..."`, `path = "..."`) is a string literal, never a
  computed value.

Example shape (adapt to what you actually did for this task):

```kotlin
fun plan(pr: String): Verdict {
    val diff = readDiff(pr)
    val findings = analyze(diff)
    return makeVerdict(findings)
}
```

This is purely additive: keep ALL your other evidence fields exactly as specified above;
just include `plan_kts` alongside them. Nothing gates on it.


## Also emit the plan as a workflow AST (`plan_ast`)

In addition to `plan_kts` and every other field above, also add a **`plan_ast`** field
to the JSON object you write to `/tmp/gh-aw/evidence.json`. This is THE SAME plan as
`plan_kts`, expressed as a restricted **workflow AST** — the form a static data-flow
verifier (e.g. Guardians) consumes to check source→sink safety before execution.

Format: a JSON object `{ "steps": [ ... ] }`, where each step is one tool call:
- `tool`: the tool/function name (e.g. `readIssue`, `readDiff`, `analyze`, `writeFile`, `curlPost`);
- `args`: an object mapping each parameter to either a literal value, or a reference to a
  prior step's result written as `{ "$ref": "<result-name>" }`;
- `result`: the name this step's output is bound to (so later steps can `$ref` it).

Example (mirrors the plan_kts example):

```json
{
  "steps": [
    { "tool": "readDiff",    "args": { "pr": { "$ref": "pr" } },         "result": "diff" },
    { "tool": "analyze",     "args": { "input": { "$ref": "diff" } },    "result": "findings" },
    { "tool": "makeVerdict", "args": { "input": { "$ref": "findings" } }, "result": "verdict" }
  ]
}
```

Keep it faithful to `plan_kts`: the same tool calls and the same data flow (each `$ref`
mirrors a Kotlin `val` dependency), with literal sink destinations. Purely additive;
nothing gates on it.

---
name: "Kotlin Plan (protocol state: plan)"
run-name: "Kotlin Plan · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
  - name: Materialize task context + stage the plan prompt
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw/agent /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cp .github/agent-factory/protocols/kotlin-plan-demo/prompts/kotlin-plan.md \
         /tmp/gh-aw/agent/kotlin-plan.md
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

# Kotlin Plan — author your plan as a verifiable `.kts` script

The full authoring spec for this task is staged at
`/tmp/gh-aw/agent/kotlin-plan.md`. **Read it first**
(`cat /tmp/gh-aw/agent/kotlin-plan.md`) and follow it exactly.

## Inputs on disk
- `/tmp/gh-aw/agent/kotlin-plan.md` — the plan-as-Kotlin authoring spec (rules + shape).
- `/tmp/gh-aw/task-context.json` — `pr`, `cid`, `iteration`, `feedback`. If `iteration`
  is > 1, fold the prior `feedback` in and repair the plan that failed the check.

## Task

Author your intended plan as a single Kotlin script per the staged spec. If the task
context names no specific task, produce a representative multi-step plan (read an
input, transform it, return a result) that obeys every rule in the spec — tool calls
as named functions, data through `val` bindings, literal sink destinations, no
provenance-breaking encodings.

Then write `/tmp/gh-aw/evidence.json` as ONE JSON object using the `edit` tool:

```json
{ "plan_kts": "fun plan(...) { ... }", "examined": ["readFile", "..."] }
```

and call `noop`. Do NOT execute any tool, read the diff, or post comments. Write
nothing else.

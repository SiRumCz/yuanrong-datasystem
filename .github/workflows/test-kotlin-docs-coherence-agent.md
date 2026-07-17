---
name: "Test Kotlin: docs coherence"
run-name: "Test Kotlin: docs coherence · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
  - name: Materialize task context + stage the plan spec
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

# Test Kotlin: docs coherence — plan your work as Kotlin (.kts); do NOT execute

You are the **docs-updated-appropriately gather** step of the code-review preflight, running here as a
**PLAN-ONLY** agent. Your real job: gather evidence on whether DOCS were updated appropriately for this change: read the diff and the docs, and check coherence.

Read the plan-as-Kotlin authoring spec staged at `/tmp/gh-aw/agent/kotlin-plan.md`
(`cat` it) and follow its rules. Then author — as a single Kotlin `.kts` script — the
plan of the tool calls you WOULD run to do that job: read the inputs you'd need,
analyze them, and produce your verdict. Make the steps realistic for THIS role, with
data flowing through named `val` bindings. Do **not** actually execute anything — no
real analysis, no tool runs — we only want the plan (to save time and tokens).

Write `/tmp/gh-aw/evidence.json` as ONE JSON object using the `edit` tool:

```json
{ "plan_kts": "fun plan(...) { ... }", "examined": ["<tool functions / sources / sinks in your plan>"] }
```

Then call `noop`. Do NOT post comments or run any analysis. Write nothing else.

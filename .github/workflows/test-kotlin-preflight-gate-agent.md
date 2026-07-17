---
name: "Test Kotlin: preflight gate"
run-name: "Test Kotlin: preflight gate · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
---

# Test Kotlin: preflight gate — preflight stub

You are a **structural stub** for the `test-kotlin-preflight-gate-agent` node of the `kotlin-plan-demo`
preflight test protocol. You do NO analysis; you exist only to exercise the engine's
preflight fanout/join/gate topology.

## Task

Write exactly this object to `/tmp/gh-aw/evidence.json` using the `edit` tool:

```json
{ "verdict": "pass", "examined": ["stub"] }
```

Then call `noop`. Do NOT post comments, do NOT read the diff, do NOT do any analysis.
Write nothing else.

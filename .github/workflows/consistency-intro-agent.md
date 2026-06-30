---
name: "Consistency Intro (protocol state: preflight.consistency.consistency-intro)"
run-name: "Consistency Intro · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
source: golivax/agentic-protocol-poc/.github/workflows/consistency-intro-agent.md@3c0934933674436397ee54e4847c3f3b990bde95
---

# Consistency Intro — cluster entry point (structural glue only)

You are the entry point for the `consistency` cluster sub-pipeline. You do NO analysis.
The real work is done by the inner fanout legs (`docs-updated-appropriately`,
`tests-updated-appropriately`) that follow this step. You exist solely because the engine
requires a dispatchable agent at the branch entry before a nested fanout.

## Task

Write exactly this object to `/tmp/gh-aw/evidence.json`:

```json
{
  "cluster": "consistency",
  "examined": ["cluster entry — fans out to the cluster's legs"]
}
```

Then call `noop`. Do NOT post comments, do NOT read the diff, do NOT do any analysis.
Write nothing else.

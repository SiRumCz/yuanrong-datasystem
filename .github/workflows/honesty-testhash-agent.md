---
name: "Honesty Test-Hash Agent (protocol state: honesty.testhash)"
run-name: "Honesty Test-Hash · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
  - name: Emit dummy test-hash evidence (stub — always passes for now)
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw
      # DUMMY: the real Sub 1 will verify a captured, non-empty test-output artifact and
      # match its sha256 against the fix agent's claimed test_output_hash. Reserved now.
      printf '' | sha256sum | awk '{print $1}' > /tmp/gh-aw/_empty_hash.txt
      HASH=$(cat /tmp/gh-aw/_empty_hash.txt)
      printf '{"check":"testhash","pass":true,"reason":"stub — test-output-hash check not yet enforced","test_output_hash":"%s"}' "$HASH" > /tmp/gh-aw/evidence.json
      cat /tmp/gh-aw/evidence.json
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
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

# Honesty Test-Hash (dummy)

A pre-agent step already wrote `/tmp/gh-aw/evidence.json` = `{ "check": "testhash", "pass": true, ... }`.
`cat` it to confirm it exists with keys `check`, `pass`, `reason`. Do not modify it. Then call `noop`.
Do not post comments or use any other safe-output. (This subworkflow is a stub; the real proof-of-work
check — verify a non-empty captured test-output artifact and match its hash — comes later.)

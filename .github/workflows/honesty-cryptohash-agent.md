---
name: "Honesty Crypto-Hash Agent (protocol state: honesty.cryptohash, code-review-honesty)"
run-name: "Honesty Crypto-Hash · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway below
  # (Tailscale Funnel, reachable from GitHub runners). gh-aw injects OPENAI_API_KEY
  # (repo secret). The agent itself needs no GitHub network access — this is a
  # parallel fanout leg, so the engine does not deliver fix evidence via
  # aw_context.inputs (#166); it and the PR data are prefetched in steps:.
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
  # sha256sum + printf are REQUIRED: the hash must be computed by the real binary,
  # never by the model (an LLM cannot compute sha256).
  bash: [ "cat:*", "echo:*", "printf:*", "sha256sum:*" ]
  edit:
steps:
  # The repo must be checked out into the workspace ROOT — gh-aw's agent job runs
  # "Configure Git credentials" before its own checkout, so a root .git must exist.
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Self-fetch fix evidence (parallel leg — no engine input delivery)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", REPO: "${{ github.repository }}",
           CID: "${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}" }
    run: |
      set -uo pipefail
      mkdir -p /tmp/gh-aw
      # Newest "Honesty-Demo Fix" run whose display title carries this cid.
      RUN_ID=$(gh run list --repo "$REPO" --workflow "honesty-demo-fix-agent.lock.yml" \
                 --json databaseId,displayTitle -L 50 \
                 -q "map(select(.displayTitle|contains(\"cid:[$CID]\")))|.[0].databaseId" 2>/dev/null || true)
      if [ -z "${RUN_ID:-}" ] || [ "$RUN_ID" = "null" ]; then
        echo "::warning::no fix run found for cid $CID; cryptohash will see empty fix evidence"
        echo '{}' > /tmp/gh-aw/fix-evidence.json
      else
        gh run download "$RUN_ID" --repo "$REPO" -n evidence -D /tmp/gh-aw/fixdl \
          && cp /tmp/gh-aw/fixdl/evidence.json /tmp/gh-aw/fix-evidence.json \
          || echo '{}' > /tmp/gh-aw/fix-evidence.json
        echo "fix evidence from run $RUN_ID"
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
---

# Honesty-Crypto-Verification — hash each fix's test output

You run AFTER the fix phase. For every fix the fix agent produced, you check
whether it carries **test output** and, if so, compute a **cryptographic
(sha256) hash** of that output so the fix is tamper-evidently verified. You do
NOT re-review code, re-run the fix, or invent anything. You emit updated fix
evidence and call `noop`; the engine's deterministic check re-verifies your
hashes and the publish hook posts the summary comment.

## Inputs (already gathered for you)

- `/tmp/gh-aw/task-context.json` — the task context. Read:
  - `.pr` — the PR number.
  - `.iteration`, `.feedback` — if `.iteration` > 1, fold the prior `.feedback`
    into this pass (a hash you produced was rejected — recompute it correctly).
- `/tmp/gh-aw/fix-evidence.json` — the upstream **fix evidence** object: `{ fixes,
  skipped, mode }`. This is a parallel fanout leg, so the engine does not deliver
  it via `.inputs.fix`; a pre-step self-fetches it from the fix run's `evidence`
  artifact instead. Each `fixes[]` entry is `{ cluster_id, path, line, rationale,
  suggested_patch, original_line?, test_output? }`. This is your work-queue; it is
  already materialized on disk — do NOT fetch it from the network yourself. If no
  fix run was found, the pre-step writes `{}`.

Read `/tmp/gh-aw/task-context.json` and `/tmp/gh-aw/fix-evidence.json` first. Do
not attempt network access.

## Step 1 — guard

If `/tmp/gh-aw/fix-evidence.json` is absent/empty, or its `.fixes` is empty,
write evidence with an empty `fixes` list (see Step 3) — there is nothing to
verify — then call `noop` and stop.

## Step 2 — verify each fix

For **each** entry in the fix evidence's `.fixes`, carry over its existing fields
(`cluster_id`, `path`, `line`, `rationale`, `suggested_patch`, `original_line`)
unchanged, echo its `test_output` through verbatim if present, and add one field
`crypto-verification-hash`:

- **If `test_output` exists and is a non-empty string** → compute its sha256 with
  the real binary and use that hex string. Run it EXACTLY like this so the
  deterministic check reproduces it byte-for-byte (no trailing newline):

  ```bash
  printf '%s' "$TEST_OUTPUT" | sha256sum | cut -d' ' -f1
  ```

  Set `crypto-verification-hash` to the 64-char hex it prints.

- **If `test_output` is missing, null, or an empty string** → set
  `crypto-verification-hash` to `null` (JSON null, not the string "null"). This
  fix has no test evidence and cannot be verified.

**Never invent, guess, or hand-compute a hash.** The only legal value is the exact
output of the `sha256sum` command above, or `null`. A hash that does not match
`sha256(test_output)` will be rejected by the `crypto-hash-valid` check and you
will be sent back to redo it. If you cannot run `sha256sum` for some fix, set its
hash to `null` rather than guessing.

## Step 3 — write evidence (always)

Write `/tmp/gh-aw/evidence.json` (the engine evidence path) as ONE JSON object,
using the `edit` tool. It is the fix list, updated with the hash:

`{"fixes":[{"cluster_id":"c1","path":"…","line":1,"rationale":"…","suggested_patch":"…","test_output":"== 3 passed ==","crypto-verification-hash":"<sha256 hex>"},{"cluster_id":"c2","path":"…","line":2,"rationale":"…","suggested_patch":"…","crypto-verification-hash":null}], "mode":"suggest"}`

Include one entry per fix (same order as the input). Write nothing else, then call
`noop`.

## Guardrails

- Process only the fixes present in the fix evidence's `.fixes`; never add, drop, or
  reorder fixes, and never alter their other fields.
- Do NOT post comments or use any safe-output other than `noop` — the engine's
  publish hook posts the green/red verification comment.
- The hash MUST come from `sha256sum`; the model must not produce hash digits itself.

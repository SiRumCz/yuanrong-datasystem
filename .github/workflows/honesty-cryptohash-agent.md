---
name: "Honesty Crypto-Hash Agent (protocol state: honesty.cryptohash)"
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
  - name: Self-fetch fix evidence + trajectory (parallel leg — no engine input delivery)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", REPO: "${{ github.repository }}",
           CID: "${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}",
           PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}" }
    run: |
      set -uo pipefail
      mkdir -p /tmp/gh-aw

      # This leg's cid is "<run>-<attempt>-per-issue-<lid>-honesty-cryptohash"
      # (agentic-engine.yml:394-396, NODE_PATH dots→dashes). <lid> == the per-issue
      # leg id == sha1(str(number))[:8] (lib.leg_id). Extract it to key the fix state file.
      LID=$(printf '%s' "$CID" | grep -oE 'per-issue-[0-9a-f]{8}-honesty' | grep -oE '[0-9a-f]{8}' | head -1)

      # HOST-AUTHORITATIVE per-leg issue: the engine stages this leg's item at fanout
      # entry to code-review/pr-<PR>/per-issue.<lid>.issue.item.json ({number,title}) on
      # agentic-state (lib.stage_item). cryptohash's proof is the fix-run/hash, so the
      # issue number is only logged here — but source it from the HOST, never from the
      # fix agent's asserted pinned_issue (the adversary this gate defends against).
      # Best-effort: a transient miss only drops the log line, never the hash.
      AUTH_ISSUE=""
      if [ -n "${LID:-}" ]; then
        ITEM=$(gh api "repos/$REPO/contents/code-review/pr-$PR/per-issue.$LID.issue.item.json?ref=agentic-state" --jq .content 2>/dev/null) || true
        AUTH_ISSUE=$(printf '%s' "${ITEM:-}" | base64 -d 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('number',''))" 2>/dev/null) || true
        [ -n "${AUTH_ISSUE:-}" ] && echo "cryptohash leg $LID host-authoritative issue=$AUTH_ISSUE"
      fi

      # 1. State file (authoritative): the engine records each phase's real run
      # id in the state branch, so read it straight from there instead of
      # inferring it from a cid. In the parallel graph this leg's OWN
      # aw_context.cid (e.g. "<run>-<attempt>-per-issue-<lid>-honesty-cryptohash") is NOT the fix run's
      # cid (e.g. "<run>-1-fix") -- matching cids against fix run titles below
      # always misses. Retried: the fix phase's state-branch push can race this
      # leg's startup.
      RUN_ID=""
      attempt=1
      while [ "$attempt" -le 3 ]; do
        CONTENT=$(gh api "repos/$REPO/contents/code-review/pr-$PR/per-issue.$LID.fix.yaml?ref=agentic-state" --jq .content 2>/dev/null) || true
        if [ -n "${CONTENT:-}" ]; then
          RUN_ID=$(printf '%s' "$CONTENT" | base64 -d 2>/dev/null | grep -oE "agent_run_id: *'?[0-9]+'?" | tail -1 | grep -oE '[0-9]+') || true
        fi
        if [ -n "${RUN_ID:-}" ]; then
          echo "fix run $RUN_ID discovered from engine state (code-review/pr-$PR/per-issue.$LID.fix.yaml)"
          break
        fi
        if [ "$attempt" -lt 3 ]; then
          sleep 5
        fi
        attempt=$((attempt + 1))
      done

      # 2. cid match (fallback only): kept for when the state file itself isn't
      # readable yet (e.g. state branch not initialized). Newest "Honesty-Demo
      # Fix" run whose display title carries this leg's cid -- usually a miss
      # in the parallel graph (see above), which is why (1) is tried first.
      if [ -z "${RUN_ID:-}" ]; then
        RUNS=$(gh run list --repo "$REPO" --workflow "fix-agent.lock.yml" -L 50 \
                 --json databaseId,displayTitle 2>/dev/null || echo '[]')
        RUN_ID=$(python3 .github/agent-factory/engine/lib.py match-run-by-cid "$RUNS" "$CID" 2>/dev/null || true)
        if [ "$RUN_ID" = "null" ]; then
          RUN_ID=""
        fi
      fi

      UNVERIFIED=""
      if [ -z "${RUN_ID:-}" ]; then
        echo "::warning::no fix run discovered (state file + cid fallback both failed) for pr $PR cid $CID"
        echo '{}' > /tmp/gh-aw/fix-evidence.json
        UNVERIFIED="fix-run-not-found"
      else
        if gh run download "$RUN_ID" --repo "$REPO" -n evidence -D /tmp/gh-aw/fixdl \
             && cp /tmp/gh-aw/fixdl/evidence.json /tmp/gh-aw/fix-evidence.json; then
          echo "fix evidence from run $RUN_ID"
          PINNED=$(python3 -c "import json;print(json.load(open('/tmp/gh-aw/fix-evidence.json')).get('pinned_issue',''))" 2>/dev/null) || true
          echo "cryptohash leg fix-agent pinned_issue=$PINNED (advisory; host-authoritative issue=${AUTH_ISSUE:-unresolved})"
          if [ -n "${PINNED:-}" ] && [ -n "${AUTH_ISSUE:-}" ] && [ "$PINNED" != "$AUTH_ISSUE" ]; then
            echo "::warning::cryptohash: fix agent asserted pinned_issue=$PINNED but host-authoritative issue is $AUTH_ISSUE (advisory only; cryptohash proof is the fix-run hash)"
          fi
        else
          echo "::warning::download failed for run $RUN_ID; cryptohash will see empty fix evidence"
          echo '{}' > /tmp/gh-aw/fix-evidence.json
        fi
        # Same run's trajectory artifact carries agent-stdio.log — the harness's
        # trusted record of what the fix agent actually executed, which the
        # agent itself cannot alter.
        if ! gh run download "$RUN_ID" --repo "$REPO" -n agent -D /tmp/gh-aw/agentdl 2>/dev/null; then
          echo "::warning::trajectory download failed for run $RUN_ID; no test run will be recognized"
          UNVERIFIED="trajectory-unavailable"
        fi
      fi

      # Never crash here: an infra failure (fix run/trajectory not
      # discoverable) must still leave recognized-test-run.json written, and
      # must carry an explicit "unverified" reason rather than a bare
      # ran:false -- ran:false unqualified means "agent genuinely did not run
      # tests", which is a different, false claim when the real cause is that
      # we couldn't find/download the evidence.
      if [ -n "$UNVERIFIED" ]; then
      python3 - "$UNVERIFIED" <<'PY'
      import sys, json
      json.dump({"ran": False, "command": "", "exit_code": None, "test_output": "",
                 "unverified": sys.argv[1]},
                open("/tmp/gh-aw/recognized-test-run.json", "w"))
      PY
      else
      # Deterministically recognize the newest real test-runner invocation from
      # the trusted trajectory (empty/missing STDIO -> ran:false, handled by
      # find_test_run itself).
      STDIO=$(find /tmp/gh-aw/agentdl -name agent-stdio.log 2>/dev/null | head -1) || true
      python3 - "${STDIO:-}" <<'PY'
      import sys, json
      sys.path.insert(0, ".github/agent-factory/protocols/code-review/checks")
      import _crypto
      log = ""
      try: log = open(sys.argv[1]).read()
      except Exception: pass
      r = _crypto.find_test_run(log)
      json.dump({"ran": r["ran"], "command": r["command"], "exit_code": r["exit_code"], "test_output": r["output"]},
                open("/tmp/gh-aw/recognized-test-run.json", "w"))
      PY
      fi
      cat /tmp/gh-aw/recognized-test-run.json
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
  - name: Assemble trusted evidence (host)
    if: always()
    run: |
      set -uo pipefail
      python3 - <<'PY'
      import json, sys
      sys.path.insert(0, ".github/agent-factory/protocols/code-review/checks")
      import _crypto
      try:
          rec = json.load(open("/tmp/gh-aw/recognized-test-run.json"))
      except Exception:
          rec = {"ran": False, "command": "", "exit_code": None, "test_output": "",
                 "unverified": "recognition-missing"}
      ev = _crypto.assemble_run_evidence(rec)
      # The agent's hash is a verified sanity check, not the source of truth.
      try:
          agent_hash = json.load(open("/tmp/gh-aw/evidence.json")).get(_crypto.HASH_FIELD)
      except Exception:
          agent_hash = None
      match = (agent_hash == ev[_crypto.HASH_FIELD])
      print(f"::notice::cryptohash agent receipt {'verified' if match else 'MISMATCH — host value used'}")
      json.dump(ev, open("/tmp/gh-aw/evidence.json", "w"))
      print(json.dumps(ev))
      PY
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 10
source: golivax/agentic-protocol-poc/.github/workflows/honesty-cryptohash-agent.md@c6ecf5dad176860d8088573b8be7f5e65e21e3dc
---

# Honesty-Crypto-Verification — hash the recognized test run

You run AFTER the fix phase. A trusted pre-step already scanned the fix
agent's own `agent-stdio.log` trajectory — a record it cannot forge — and
deterministically recognized whether a real test-runner command ran and, if
so, captured its exact command/exit code/output. Your ONLY job is to copy
that recognized run through unchanged and compute a **cryptographic (sha256)
hash** of its output so it is tamper-evidently verified. You do NOT decide
whether a test ran, re-review code, or re-run anything — that recognition
already happened, deterministically, before you started. You emit evidence
and call `noop`; the engine's deterministic check re-verifies your hash and
the merge hook posts the summary comment.

## Inputs (already gathered for you)

- `/tmp/gh-aw/task-context.json` — the task context. Read:
  - `.pr` — the PR number.
  - `.iteration`, `.feedback` — if `.iteration` > 1, fold the prior `.feedback`
    into this pass (a hash you produced was rejected — recompute it correctly).
- `/tmp/gh-aw/recognized-test-run.json` — the single recognized test run:
  `{"ran": bool, "command": str, "exit_code": int|null, "test_output": str}`.
  A trusted pre-step produced this by scanning the fix run's `agent-stdio.log`
  trajectory artifact for the newest real test-runner invocation. This is not
  your work-queue to interpret — it is a finished verdict you only read and
  carry forward. If no fix run/trajectory was found, `ran` is `false` and the
  other fields are empty/null.

Read `/tmp/gh-aw/task-context.json` and `/tmp/gh-aw/recognized-test-run.json`
first. Do not attempt network access.

## Step 1 — copy the recognized run verbatim

Copy `ran`, `command`, `exit_code`, and `test_output` from
`/tmp/gh-aw/recognized-test-run.json` UNCHANGED. Do not re-judge whether a
test ran, second-guess the recognizer, or edit these fields — that decision
was already made, deterministically, by the trusted pre-step.

## Step 2 — hash it

- **If `ran` is `true` and `test_output` is a non-empty string** → compute its
  sha256 with the real binary and use that hex string. Run it EXACTLY like this
  so the deterministic check reproduces it byte-for-byte (no trailing newline):

  ```bash
  printf '%s' "$TEST_OUTPUT" | sha256sum | cut -d' ' -f1
  ```

  Set `crypto-verification-hash` to the 64-char hex it prints.

- **Otherwise** (`ran` is `false`, or `test_output` is missing/empty) → set
  `crypto-verification-hash` to `null` (JSON null, not the string "null").
  There is no test evidence to verify.

**Never invent, guess, or hand-compute a hash.** The only legal value is the exact
output of the `sha256sum` command above, or `null`. A hash that does not match
`sha256(test_output)` will be rejected by the `crypto-hash-valid` check and you
will be sent back to redo it. If you cannot run `sha256sum`, set the hash to
`null` rather than guessing.

## Step 3 — write evidence (always)

Write `/tmp/gh-aw/evidence.json` (the engine evidence path) as ONE JSON object,
using the `edit` tool — the recognized run, plus your hash:

`{"ran":true,"command":"pytest -q","exit_code":0,"test_output":"== 1 passed ==","crypto-verification-hash":"<sha256 hex>"}`

or, when no test ran:

`{"ran":false,"command":"","exit_code":null,"test_output":"","crypto-verification-hash":null}`

Write nothing else, then call `noop`.

## Guardrails

- `ran`, `command`, `exit_code`, and `test_output` MUST equal
  `/tmp/gh-aw/recognized-test-run.json` verbatim — never add, drop, or alter them.
- Do NOT post comments or use any safe-output other than `noop` — the engine's
  publish hook posts the green/red verification comment.
- The hash MUST come from `sha256sum`; the model must not produce hash digits itself.

## Note

A trusted host post-step runs after you and rebuilds `/tmp/gh-aw/evidence.json`
directly from `/tmp/gh-aw/recognized-test-run.json` plus a host-computed
sha256 — it does not read your copy of `ran`/`command`/`exit_code`/
`test_output`. Your hash is checked against that host-computed value only as a
sanity check on your work; it is not the source of truth downstream. You must
still compute the hash into your own `evidence.json` exactly as described
above — a mismatch is logged and your value is discarded in favor of the
host's trusted fields and hash.

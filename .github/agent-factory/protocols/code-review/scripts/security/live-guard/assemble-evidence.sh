#!/usr/bin/env bash
# Fold the live guard's own records into the engine's evidence artifact.
#
# Runs from `post-steps:` — AFTER the agent, as an ordinary workflow step. The
# agent cannot execute it, so it cannot fabricate "the guard ran".
#
# Usage: assemble-evidence.sh <log-dir> <evidence.json> <step-name>
#
# The deny leg's agent is STOPPED by the hook (continue:false) and therefore
# never writes evidence.json. Synthesising it here is required, not defensive:
# a missing artifact would make a caught violation look like an infrastructure
# failure, which is the exact confusion this protocol removes.
set -uo pipefail
LOG_DIR="${1:?log dir}"; EV="${2:?evidence path}"; STEP="${3:-guard}"
mkdir -p "$(dirname "$EV")"
[ -s "$EV" ] && jq -e . "$EV" >/dev/null 2>&1 || echo '{}' > "$EV"

LIVENESS=$(cat "$LOG_DIR"/liveness/*.json 2>/dev/null | jq -s '.[0] // {}')
# NOTE: `jq -s '.' <missing-file> 2>/dev/null || echo '[]'` is NOT safe here --
# jq prints its OWN "[]" to stdout even when the file can't be opened (then
# exits 2), so `||` would append a SECOND "[]", yielding "[]\n[]" and making
# --argjson below fail on every run where a log file hasn't been written yet
# (e.g. the allow leg's incidents.jsonl, the expected-pass case with zero
# incidents). Capture stdout via the assignment itself and only fall back on
# a genuine jq failure, discarding whatever partial stdout jq produced.
if ! DECISIONS=$(jq -s '.' "$LOG_DIR/decisions.jsonl" 2>/dev/null); then
  DECISIONS='[]'
fi
if ! INCIDENTS=$(jq -s '.' "$LOG_DIR/incidents.jsonl" 2>/dev/null); then
  INCIDENTS='[]'
fi

# `examined` is the evidence contract's negative-attestation trace: the
# identifiers a coverage check settles "the agent actually read the code"
# against. It belongs to the AGENT, so the guard's own sources go inside
# live_guard and the agent's root trace is left alone. The root value is
# supplied only when the agent left none -- the deny leg, stopped before it
# could write evidence at all, and cedar-on-hook-test's guard legs, whose
# schema wants a root trace and whose only reader is the ledger.
#
# Overwriting it unconditionally (as this did until 2026-08-13) is worse than
# a schema violation: `_coherence.py` accepts any non-empty list, so a leg
# would PASS its coverage check while attesting that the agent examined
# `decisions.jsonl`. The 5 pilot review legs could not surface it --
# review.evidence.schema.json has no root `examined` -- but 13 of the 20
# nodes in the wider rollout require one.
jq --argjson liveness "$LIVENESS" --argjson decisions "$DECISIONS" \
   --argjson incidents "$INCIDENTS" --arg step "$STEP" \
  '(["liveness", "decisions.jsonl", "incidents.jsonl"]) as $read
   | . + {step: $step,
          live_guard: {liveness: $liveness, decisions: $decisions,
                       incidents: $incidents, examined: $read}}
   | if (.examined | type) == "array" and (.examined | length) > 0
     then . else .examined = $read end' \
  "$EV" > "$EV.tmp" && mv "$EV.tmp" "$EV"
echo "--- live_guard:"; jq '{enforced: (.live_guard.liveness.engines // {}), incidents: (.live_guard.incidents | length)}' "$EV"

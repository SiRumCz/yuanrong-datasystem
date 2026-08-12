# `inputs.legs` carries the fork's per-leg reduce rows -- an ARRAY, one row
# per leg, exactly as `lib.collect_fork_evidence` returns and `next.py`'s
# `_resolve_from_fork_input` json.dumps verbatim: {leg_id, key, state,
# evidence}. Each leg's guard evidence is nested at `.evidence.live_guard...`,
# NOT flattened onto the row -- see tests/engine/test_dispatched_code_from_fork.py.
(($ctx[0].inputs.legs) // []) as $legs
| {step: "record",
   legs: ($legs | map(
            . as $leg
            | (($leg.evidence.live_guard.liveness.engines) // {}) as $engines
            # `enforced` must agree with checks/live-guard-enforced.py's own
            # claim order: a `cedar` engine key present is NOT enough -- an
            # `error:`-prefixed key means the guard fell back to fail-open
            # (degraded), and that check fails the run even though `engines`
            # is non-empty. Without the second half, a run with
            # engines={"cedar":3,"error:decide":1} would record
            # `enforced: true` here for a run the check declares unenforceable.
            | {leg: $leg.leg_id,
               enforced: (($engines | keys | any(startswith("cedar")))
                          and (($engines | keys | any(startswith("error:"))) | not)),
               decisions: ((($leg.evidence.live_guard.decisions) // []) | length),
               incidents: (($leg.evidence.live_guard.incidents) // [])})),
   examined: ($legs | map(.leg_id))}

#!/usr/bin/env python3
"""Check: the gate's consolidated evidence carries exactly one well-formed cell
per DECLARED leg. This is the gate node's mandatory passing form-check (a node
with no passing iterate-verdict can never reach `done`). The declared leg set
comes from CHECK_PARAMS.legs (the gate node's params) — never hardcoded, so the
same check serves Phase A (3 chain legs) and later phases (6 legs).

A cell is well-formed iff it is an object with a non-empty `leg`, a non-empty
`verdict`, and a `scope` object. Every declared leg must appear exactly once;
no cell may name an undeclared leg.

ABI: preflight-gate-coverage.py <evidence.json> <diff.txt> <changed-files.txt>
"""
import json
import os
import sys


def main():
    try:
        params = json.loads(os.environ.get("CHECK_PARAMS", "") or "{}")
        legs = params.get("legs") if isinstance(params, dict) else None
    except ValueError:
        legs = None
    if not isinstance(legs, list) or not legs:
        print(json.dumps({"check": "preflight-gate-coverage", "pass": False,
                          "feedback": "no `legs` in CHECK_PARAMS (gate must declare its leg set)"}))
        return
    expected = list(legs)

    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        print(json.dumps({"check": "preflight-gate-coverage", "pass": False,
                          "feedback": f"evidence unreadable / not JSON: {exc}"}))
        return

    cells = ev.get("legs") if isinstance(ev, dict) else None
    if not isinstance(cells, list):
        print(json.dumps({"check": "preflight-gate-coverage", "pass": False,
                          "feedback": "evidence.legs must be an array of leg cells"}))
        return

    seen = {}
    malformed = []
    for c in cells:
        if not isinstance(c, dict) or not c.get("leg"):
            malformed.append("a cell with no `leg`")
            continue
        name = c["leg"]
        if not c.get("verdict") or not isinstance(c.get("scope"), dict):
            malformed.append(name)
        seen[name] = seen.get(name, 0) + 1

    problems = []
    missing = [leg for leg in expected if leg not in seen]
    dups = sorted({leg for leg, n in seen.items() if n > 1})
    unexpected = sorted(leg for leg in seen if leg not in expected)
    if missing:    problems.append(f"missing leg cell(s): {missing}")
    if dups:       problems.append(f"duplicate leg cell(s): {dups}")
    if unexpected: problems.append(f"unexpected leg cell(s): {unexpected}")
    if malformed:  problems.append(f"malformed cell(s) (need leg+verdict+scope): {sorted(set(malformed))}")

    if problems:
        print(json.dumps({"check": "preflight-gate-coverage", "pass": False,
                          "feedback": "gate coverage off: " + "; ".join(problems)}))
    else:
        print(json.dumps({"check": "preflight-gate-coverage", "pass": True,
                          "feedback": f"one well-formed cell per leg ({expected})."}))


if __name__ == "__main__":
    main()

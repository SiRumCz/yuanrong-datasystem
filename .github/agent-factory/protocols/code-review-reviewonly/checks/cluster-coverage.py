#!/usr/bin/env python3
"""Check: the rollup agent's cluster evidence carries exactly one well-formed cell
per DECLARED inner leg. This is the cluster node's mandatory passing form-check.
The declared leg set comes from CHECK_PARAMS.legs (the cluster node's params) —
never hardcoded, so the same check serves any cluster regardless of how many
inner legs it fans out to.

Option 2 (grades-only): a cell is well-formed iff it is an object with a
non-empty `leg` and — when present — a list `graded_findings`. The rollup is an
LLM with the same echo-unreliability as the inner judges, so this check no
longer requires it to copy per-leg `scope`/`gather_verdict`: conclude-preflight
reads those straight from each leg's persisted gather evidence, and treats the
rollup's `graded_findings` as escalation-only (additive, can only block harder
than the deterministic floor). Every declared leg must still appear exactly
once; no cell may name an undeclared leg.

ABI: cluster-coverage.py <evidence.json> <diff.txt> <changed-files.txt>
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
        print(json.dumps({"check": "cluster-coverage", "pass": False,
                          "feedback": "no `legs` in CHECK_PARAMS (cluster must declare its leg set)"}))
        return
    expected = list(legs)

    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        print(json.dumps({"check": "cluster-coverage", "pass": False,
                          "feedback": f"evidence unreadable / not JSON: {exc}"}))
        return

    cells = ev.get("legs") if isinstance(ev, dict) else None
    if not isinstance(cells, list):
        print(json.dumps({"check": "cluster-coverage", "pass": False,
                          "feedback": "evidence.legs must be an array of leg cells"}))
        return

    seen = {}
    malformed = []
    for c in cells:
        if not isinstance(c, dict) or not c.get("leg"):
            malformed.append("a cell with no `leg`")
            continue
        name = c["leg"]
        gf = c.get("graded_findings")
        if gf is not None and not isinstance(gf, list):
            malformed.append(name)
        seen[name] = seen.get(name, 0) + 1

    problems = []
    missing = [leg for leg in expected if leg not in seen]
    dups = sorted({leg for leg, n in seen.items() if n > 1})
    unexpected = sorted(leg for leg in seen if leg not in expected)
    if missing:    problems.append(f"missing leg cell(s): {missing}")
    if dups:       problems.append(f"duplicate leg cell(s): {dups}")
    if unexpected: problems.append(f"unexpected leg cell(s): {unexpected}")
    if malformed:  problems.append(f"malformed cell(s) (need leg; graded_findings must be a list if present): {sorted(set(malformed))}")

    if problems:
        print(json.dumps({"check": "cluster-coverage", "pass": False,
                          "feedback": "cluster coverage off: " + "; ".join(problems)}))
    else:
        print(json.dumps({"check": "cluster-coverage", "pass": True,
                          "feedback": f"one well-formed cell per leg ({expected})."}))


if __name__ == "__main__":
    main()

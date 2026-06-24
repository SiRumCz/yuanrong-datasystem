#!/usr/bin/env python3
"""Conclude hook for the preflight phase. Rolls up the agent's adherence verdicts
with the engine's blocking signal into clear/blocked, and writes a custody-shaped
verdict.json payload for publish-verdict.

ABI: conclude-preflight.py <evidence.json> <instance-key>;  env BLOCKING ("1"/"0").
Prints {"conclusion","summary","blocked"}. blocked = BLOCKING OR any adherence fail."""
import json
import os
import sys


def main():
    ev_path = sys.argv[1]
    blocking = os.environ.get("BLOCKING", "") == "1"
    try:
        with open(ev_path) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    checks = evidence.get("checks", []) if isinstance(evidence, dict) else []

    adherence_fail = any(isinstance(c, dict) and c.get("status") in ("fail", "error") for c in checks)
    blocked = bool(blocking or adherence_fail)
    status = "blocked" if blocked else "clear"

    # custody-shaped verdict.json payload (records[] + verdict + meta echo).
    counts = {"pass": 0, "fail": 0, "warn": 0, "todo": 0, "error": 0, "skipped": 0}
    for c in checks:
        st = c.get("status") if isinstance(c, dict) else None
        if st in counts:
            counts[st] += 1
    records = [{"type": "check", **c} for c in checks if isinstance(c, dict)]
    records.append({"type": "verdict", "status": status, "counts": counts,
                    "blocking": bool(blocking)})
    payload = {"records": records}
    # meta: pr number from the instance-key "pr-N" (head_sha unknown here → empty).
    inst = sys.argv[2] if len(sys.argv) > 2 else ""
    if inst.startswith("pr-") and inst[3:].isdigit():
        payload["meta"] = {"pr_number": int(inst[3:]), "head_sha": os.environ.get("HEAD_SHA", "")}

    out_path = os.environ.get("VERDICT_OUT", "/tmp/gh-aw/verdict.json")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump(payload, fh)
    except OSError:
        pass

    if blocked:
        summary = "Preflight blocked: " + (
            "a required spec/plan is missing" if blocking else "code does not adhere to the declared spec/plan")
    else:
        summary = "Preflight clear."
    print(json.dumps({"conclusion": "blocked" if blocked else "clear",
                      "summary": summary, "blocked": blocked}))


if __name__ == "__main__":
    main()

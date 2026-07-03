#!/usr/bin/env python3
"""ledger-wellformed (layer 1) — per-item completeness + valid enums + justified
blast_radius/reversibility + ASSUMPTION ⇒ verified:true. Form only; never judges
whether a rating is calibrated. Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

CATEGORIES = {"DECISION", "ASSUMPTION", "UNKNOWN", "DEFERRED", "DEVIATION"}
CONF = {"high", "medium", "low"}
BLAST = {"low", "medium", "high"}
REV = {"reversible", "costly", "irreversible"}
SCALARS = ("what", "why", "what_i_did", "revisit_if")


def emit(ok, feedback):
    print(json.dumps({"check": "ledger-wellformed", "pass": ok, "feedback": feedback}))


def main():
    ev = _common.load_evidence(sys.argv[1] if len(sys.argv) > 1 else "")
    ledger = ev.get("ledger")
    if not isinstance(ledger, list) or not ledger:
        emit(False, "ledger missing or empty")
        return
    problems = []
    for i, it in enumerate(ledger):
        tag = it.get("id", f"[{i}]") if isinstance(it, dict) else f"[{i}]"
        if not isinstance(it, dict):
            problems.append(f"{tag}: not an object"); continue
        if it.get("category") not in CATEGORIES:
            problems.append(f"{tag}: category {it.get('category')!r} not in {sorted(CATEGORIES)}")
        for f in SCALARS:
            if not _common.NON_TRIVIAL(it.get(f)):
                problems.append(f"{tag}: field {f!r} missing/trivial")
        if it.get("confidence") not in CONF:
            problems.append(f"{tag}: confidence {it.get('confidence')!r} invalid")
        for axis, allowed in (("blast_radius", BLAST), ("reversibility", REV)):
            obj = it.get(axis)
            if not isinstance(obj, dict):
                problems.append(f"{tag}: {axis} missing/not-object"); continue
            if obj.get("level") not in allowed:
                problems.append(f"{tag}: {axis}.level {obj.get('level')!r} invalid")
            if not _common.NON_TRIVIAL(obj.get("why")):
                problems.append(f"{tag}: {axis}.why missing/trivial (must justify the level)")
        if it.get("category") == "ASSUMPTION" and it.get("verified") is not True:
            problems.append(f"{tag}: ASSUMPTION must carry verified:true (verify the code fact)")
    if problems:
        emit(False, "; ".join(problems[:8]))
    else:
        emit(True, "")


if __name__ == "__main__":
    main()

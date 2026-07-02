#!/usr/bin/env python3
"""ledger-consistent (layer 2) — rule-based contradictions (deterministic, not
judgments). R1: UNKNOWN ⇒ confidence==low. R2: ledger ids unique.
Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402


def emit(ok, feedback):
    print(json.dumps({"check": "ledger-consistent", "pass": ok, "feedback": feedback}))


def main():
    ev = _common.load_evidence(sys.argv[1] if len(sys.argv) > 1 else "")
    ledger = ev.get("ledger")
    if not isinstance(ledger, list) or not ledger:
        emit(False, "ledger missing or empty")
        return
    problems = []
    seen = set()
    for it in ledger:
        if not isinstance(it, dict):
            continue
        tag = it.get("id", "?")
        if it.get("category") == "UNKNOWN" and it.get("confidence") != "low":
            problems.append(f"{tag}: UNKNOWN with confidence {it.get('confidence')!r} "
                            f"— UNKNOWN is low-confidence by definition")
        if tag in seen:
            problems.append(f"duplicate ledger id {tag!r}")
        seen.add(tag)
    if problems:
        emit(False, "; ".join(problems[:8]))
    else:
        emit(True, "")


if __name__ == "__main__":
    main()

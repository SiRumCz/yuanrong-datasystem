#!/usr/bin/env python3
"""Check: the sha256 the crypto-verify agent appended to each fix is genuine.

An LLM cannot compute sha256, so the `crypto-verification-hash` the agent writes
is only a claim. This deterministic gate recomputes each hash for real and
rejects the evidence if any is wrong — the agent cannot fake "cryptographically
verified". Rule, per fix:
  - test_output present & non-empty  -> hash MUST equal sha256(test_output)
  - test_output missing/empty        -> hash MUST be null (an unverifiable fix)

A missing test_output is NOT a failure here (null is the correct value); the
red/green *reporting* of untested fixes is the conclude hook's job. This check
only fails when the agent LIES about a hash — a mismatch, a hash where there
should be null, or null where there should be a hash.

ABI: crypto-hash-valid.py <evidence.json> <diff.txt> <changed-files.txt>
Prints one {"check","pass","feedback"} object to stdout and always exits 0.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _crypto  # noqa: E402

CHECK = "crypto-hash-valid"


def _emit(passed, feedback):
    print(json.dumps({"check": CHECK, "pass": passed, "feedback": feedback}, ensure_ascii=False))


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        _emit(False, f"evidence unreadable / not JSON: {exc}")
        return
    if not isinstance(ev, dict):
        _emit(False, "evidence is not a JSON object")
        return
    if not isinstance(ev.get("fixes"), list):
        _emit(False, "`fixes` must be an array")
        return

    problems = []
    for v in _crypto.classify_all(ev):
        if v["hash_ok"]:
            continue
        sid = _crypto.short_id(v)
        if v["expected"] is None:
            problems.append(
                f"{sid}: has no/empty test_output so `{_crypto.HASH_FIELD}` must be null, "
                f"got {v['claimed']!r}")
        elif v["claimed"] is None:
            problems.append(
                f"{sid}: has test_output but `{_crypto.HASH_FIELD}` is null "
                f"(expected sha256 {v['expected'][:12]}…)")
        else:
            problems.append(
                f"{sid}: `{_crypto.HASH_FIELD}` {str(v['claimed'])[:12]}… does not match "
                f"sha256(test_output) {v['expected'][:12]}… (fabricated/incorrect hash)")

    if problems:
        _emit(False, "crypto hash invalid: " + "; ".join(problems[:6]))
    else:
        _emit(True, "")


if __name__ == "__main__":
    main()

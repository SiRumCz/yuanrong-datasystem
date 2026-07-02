#!/usr/bin/env python3
"""Form-check for the security-gather agent's evidence.

Re-derives the verdict from engine_report.violations according to the
deterministic rule, then asserts evidence.verdict matches. Also asserts
the required sub-objects (cedar, guardians, engine_report) are present.

Verdict rule (deterministic — NOT a judgment):
  LOCKED_VIOLATION  iff engine_report.violations contains any entry with locked:true
  n/a               if engines could not run (no `violations` field in engine_report,
                    or engine_report absent) — fail-OPEN, never silent PASS
  PASS              otherwise (violations present, none have locked:true)

Zone 3 — read-only, holds no credentials.
ABI: security-gather-coverage.py <evidence.json> <diff.txt> <changed-files.txt>
"""
import json
import os
import sys

VALID_VERDICTS = {"PASS", "LOCKED_VIOLATION", "n/a"}
CHECK_NAME = "security-gather-coverage"


def _emit(ok, fb):
    print(json.dumps({"check": CHECK_NAME, "pass": ok, "feedback": fb}))


def _recompute_verdict(engine_report):
    """Recompute verdict from engine_report according to the deterministic rule."""
    if not isinstance(engine_report, dict):
        return "n/a"
    violations = engine_report.get("violations")
    if violations is None:
        # No violations field — engines could not run
        return "n/a"
    if not isinstance(violations, list):
        # Malformed violations — treat as engines absent
        return "n/a"
    for v in violations:
        if isinstance(v, dict) and v.get("locked") is True:
            return "LOCKED_VIOLATION"
    return "PASS"


def main():
    # Load evidence
    evidence_path = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        with open(evidence_path) as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        _emit(False, f"evidence unreadable / not JSON: {exc}")
        return

    if not isinstance(ev, dict):
        _emit(False, "evidence is not a JSON object")
        return

    # Assert required sub-objects are present
    for key in ("cedar", "guardians", "engine_report"):
        val = ev.get(key)
        if val is None:
            _emit(False, f"missing required field: {key!r}")
            return
        if not isinstance(val, dict):
            _emit(False, f"{key!r} must be a JSON object, got {type(val).__name__}")
            return

    engine_report = ev["engine_report"]

    # Validate verdict enum
    verdict = ev.get("verdict")
    if verdict not in VALID_VERDICTS:
        _emit(False, f"verdict {verdict!r} not in allowed enum {sorted(VALID_VERDICTS)}")
        return

    # Recompute and compare
    recomputed = _recompute_verdict(engine_report)
    if verdict != recomputed:
        _emit(False, (
            f"verdict mismatch: evidence says {verdict!r} "
            f"but recompute from engine_report gives {recomputed!r}"
        ))
        return

    examined = ev.get("examined")
    _emit(True, (
        f"security-gather form valid: verdict={verdict!r}, "
        f"engines present, examined={len(examined) if isinstance(examined, list) else '?'} item(s)."
    ))


if __name__ == "__main__":
    main()

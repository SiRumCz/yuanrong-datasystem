#!/usr/bin/env python3
"""Form-check for a <leg>-judge (Option 2 — grades-only contract).

The judge's load-bearing job is to GRADE the gather's findings (severity +
rationale). The leg's scope + gather_verdict are NOT trusted from the judge's
echo: conclude-preflight reads them straight from the persisted gather evidence
(the deterministic source the engine already holds). So this check validates
ONLY the grade form; it no longer rejects on a missing/empty/mismatched scope,
gather_verdict, or examined echo. That echo was the live-observed exhaustion
mode — an LLM judge cannot reliably copy gather-derived facts a deterministic
check already recomputes, and copying them proves nothing the gather's own
coverage check has not already verified.

Validates:
  * CHECK_PARAMS carries {leg, mode} (per-leg identity; used only for the message).
  * evidence is a JSON object.
  * graded_findings, when present, is a list and every entry is an object with a
    non-empty `ref` and a `severity` in {blocking, advisory, noise}. An absent
    graded_findings is treated as "nothing to grade" and passes.

ABI: judge-coverage.py <evidence.json> <diff.txt> <changed-files.txt>
Reads CHECK_PARAMS env. Zone 3 — holds no creds. ALWAYS exit 0.
"""
import json
import os
import sys

NAME = "judge-coverage"
SEVS = {"blocking", "advisory", "noise"}


def _emit(ok, fb):
    print(json.dumps({"check": NAME, "pass": ok, "feedback": fb}))


def main():
    try:
        params = json.loads(os.environ.get("CHECK_PARAMS", "") or "{}")
        mode = params.get("mode") if isinstance(params, dict) else None
        leg = params.get("leg") if isinstance(params, dict) else None
    except ValueError:
        mode = leg = None
    if not mode or not leg:
        _emit(False, "CHECK_PARAMS must carry {leg, mode}")
        return

    try:
        ev = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else {}
    except (OSError, ValueError) as exc:
        _emit(False, f"evidence unreadable: {exc}")
        return
    if not isinstance(ev, dict):
        _emit(False, "evidence must be a JSON object")
        return

    # grade form — the only load-bearing thing the judge produces.
    graded = ev.get("graded_findings")
    if graded is None:
        graded = []
    if not isinstance(graded, list):
        _emit(False, "graded_findings must be an array when present")
        return
    for g in graded:
        if not isinstance(g, dict) or not g.get("ref") or g.get("severity") not in SEVS:
            _emit(False, "each graded finding needs {ref (non-empty), severity in blocking|advisory|noise}")
            return

    _emit(True, f"{leg} ({mode}): {len(graded)} finding(s) graded (grades-only contract).")


if __name__ == "__main__":
    main()

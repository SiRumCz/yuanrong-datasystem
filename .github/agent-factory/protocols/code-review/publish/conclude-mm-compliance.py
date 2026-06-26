#!/usr/bin/env python3
"""Conclude hook for the mental-model compliance gate.

Blocks the pipeline when the agent judged the PR to DIVERGE from the stored mental
model (verdict == "diverges" or any recorded divergences), or when the engine's own
BLOCKING signal is set. Clear (advisory-pass) otherwise. A blocked verdict + the
node's `on_blocked: halt` halts the run until a write-access human `/override`s.

ABI: conclude-mm-compliance.py <evidence.json> <instance-key>; env BLOCKING ("1"/"0").
Prints {"conclusion","summary","blocked"}.
"""
import json
import os
import sys


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    blocking = os.environ.get("BLOCKING", "") == "1"
    try:
        with open(ev_path, encoding="utf-8") as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        ev = {}

    verdict = ev.get("verdict") if isinstance(ev, dict) else None
    divergences = ev.get("divergences") if isinstance(ev, dict) else []
    n = len(divergences) if isinstance(divergences, list) else 0
    diverges = (verdict == "diverges") or (n > 0)
    blocked = bool(blocking or diverges)

    if blocked and diverges:
        summary = (f"Mental-model compliance: {n} divergence(s) — halting until the code "
                   "complies, the mental model is updated, or a maintainer /overrides.")
    elif blocked:
        summary = "Mental-model compliance blocked (engine signal)."
    else:
        summary = "Mental-model compliance: consistent with the stored mental model."

    print(json.dumps({"conclusion": "blocked" if blocked else "clear",
                      "summary": summary, "blocked": blocked}))


if __name__ == "__main__":
    main()

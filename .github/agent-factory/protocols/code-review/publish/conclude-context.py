#!/usr/bin/env python3
"""Conclude hook for the advisory context phase.

The context phase exports conversation composition evidence for later inspection.
It must never block the code-review pipeline, so this hook always reports
blocked:false while summarizing whether a transcript was present.
"""
import json
import sys


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        with open(ev_path, encoding="utf-8") as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}

    phases = evidence.get("phases") if isinstance(evidence, dict) else []
    transcript_present = bool(evidence.get("transcript_present")) if isinstance(evidence, dict) else False
    phase_count = len(phases) if isinstance(phases, list) else 0

    if transcript_present:
        summary = f"Context export captured {phase_count} phase bucket(s)."
    else:
        summary = "Context export found no committed transcript."

    print(json.dumps({"conclusion": "neutral", "summary": summary, "blocked": False}))


if __name__ == "__main__":
    main()

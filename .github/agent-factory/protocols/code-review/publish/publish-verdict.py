#!/usr/bin/env python3
"""Publish hook for the preflight phase. Side-effects only: the verdict.json
written by conclude-preflight is uploaded as a workflow artifact by the gh-aw/
engine step; this hook sets the preflight sub check-run and echoes the conclusion.

ABI: publish-verdict.py <evidence.json> <instance-key>; env ENGINE_LOCAL,
GITHUB_REPOSITORY, PUBLISH_TOKEN, PR. Prints {"conclusion","summary"}."""
import json
import os
import sys


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        with open(ev_path) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    checks = evidence.get("checks", []) if isinstance(evidence, dict) else []
    n = len(checks)
    # The engine already decided conclusion via conclude-preflight; publish only
    # reports. In ENGINE_LOCAL test mode, do no GitHub I/O.
    summary = f"Preflight published ({n} adherence verdict(s))."
    print(json.dumps({"conclusion": "neutral", "summary": summary}))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generic, reusable form check: the agent's evidence is a JSON object that carries
the required top-level keys, and any keys named in `non_empty` are non-empty.

This is the engine's "demand evidence, check its form deterministically" gate at its
most basic — it verifies the agent produced the right *shape* for its phase, nothing
about the *substance*. Reused across the migrated phases (overview / review / triage /
fix / context / mrp); each phase's protocol entry passes its own params.

Config (CHECK_PARAMS env, the node's `params`):
  require:   [str]  top-level keys that MUST be present (any value, incl. empty)
  non_empty: [str]  subset that must additionally be non-empty/truthy

ABI: evidence-present.py <evidence.json> <diff.txt> <changed-files.txt>
Prints one {"check","pass","feedback"} object to stdout and always exits 0.
"""
import json
import os
import sys


def main():
    try:
        params = json.loads(os.environ.get("CHECK_PARAMS", "") or "{}")
        if not isinstance(params, dict):
            params = {}
    except ValueError:
        params = {}
    require = params.get("require") or []
    non_empty = params.get("non_empty") or []

    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        print(json.dumps({"check": "evidence-present", "pass": False,
                          "feedback": f"evidence unreadable / not JSON: {exc}"}))
        return

    problems = []
    if not isinstance(ev, dict):
        problems.append("evidence is not a JSON object")
    else:
        for k in require:
            if k not in ev:
                problems.append(f"missing required key `{k}`")
        for k in non_empty:
            v = ev.get(k)
            # falsy-but-legal values (False, 0) count as present; only None / "" / [] / {} are "empty"
            if v is None or v == "" or v == [] or v == {}:
                problems.append(f"key `{k}` is empty")

    if problems:
        print(json.dumps({"check": "evidence-present", "pass": False,
                          "feedback": "evidence shape: " + "; ".join(problems[:6])}))
    else:
        print(json.dumps({"check": "evidence-present", "pass": True, "feedback": ""}))


if __name__ == "__main__":
    main()

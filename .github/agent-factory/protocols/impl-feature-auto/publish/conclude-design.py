#!/usr/bin/env python3
"""Conclude hook for the `design` phase. Enforces the protocol's headline
guarantee — no spec/plan ⇒ no PR — by turning the engine's block signal into a
halt. The block decision is entirely encoded in the BLOCKING env (set from the
`spec-present` / `plan-present` block-severity check verdicts); this hook does
not re-read evidence.

ABI: conclude-design.py <evidence.json> <instance-key>;  env BLOCKING ("1"/"0").
Prints {"conclusion","summary","blocked"}. With `on_blocked: "halt"` on the
design node, blocked=true halts the pipeline before `implement`."""
import json
import os
import sys


def main():
    _ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    _instance = sys.argv[2] if len(sys.argv) > 2 else ""
    blocked = os.environ.get("BLOCKING", "") == "1"
    summary = ("Design blocked: a required spec or plan is missing."
               if blocked else "Design clear.")
    print(json.dumps({"conclusion": "blocked" if blocked else "clear",
                      "summary": summary, "blocked": blocked}))


if __name__ == "__main__":
    main()

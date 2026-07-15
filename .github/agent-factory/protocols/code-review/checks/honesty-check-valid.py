#!/usr/bin/env python3
"""Check: honesty sub evidence validates against honesty-check.evidence.schema.json.
ABI: honesty-check-valid <evidence.json> <diff.txt> <changed-files.txt> -> {check,pass,feedback}, exit 0."""
import json, os, sys

def main(argv):
    ev_path = argv[0] if argv else ""
    result = {"check": "honesty-check-valid", "pass": False, "feedback": ""}
    try:
        ev = json.load(open(ev_path))
    except Exception as e:
        result["feedback"] = f"unreadable evidence: {e}"
        print(json.dumps(result)); return 0
    if not isinstance(ev, dict):
        result["feedback"] = "evidence is not an object"
        print(json.dumps(result)); return 0
    if ev.get("check") not in ("testhash", "fixverify"):
        result["feedback"] = "check must be 'testhash' or 'fixverify'"
        print(json.dumps(result)); return 0
    if not isinstance(ev.get("pass"), bool):
        result["feedback"] = "pass must be a boolean"
        print(json.dumps(result)); return 0
    if not isinstance(ev.get("reason"), str) or not ev["reason"].strip():
        result["feedback"] = "reason must be a non-empty string"
        print(json.dumps(result)); return 0
    result["pass"] = True
    print(json.dumps(result)); return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

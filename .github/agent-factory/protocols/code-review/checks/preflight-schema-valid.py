#!/usr/bin/env python3
"""Check: the preflight evidence has the required shape — a `checks` list whose
entries each carry an `id` and a `status` in {pass,fail,warn}, plus an `examined`
list. Reports the shape only (coverage/anchors are other checks).
Usage: schema-valid.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import sys

OK_STATUS = {"pass", "fail", "warn"}


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            evidence = json.load(fh)
    except (OSError, ValueError) as exc:
        print(json.dumps({"check": "schema-valid", "pass": False,
                          "feedback": f"evidence unreadable/not JSON: {exc}"}))
        return
    problems = []
    if not isinstance(evidence, dict):
        problems.append("evidence is not a JSON object")
    else:
        checks = evidence.get("checks")
        if not isinstance(checks, list):
            problems.append("missing or non-list `checks`")
        else:
            for i, c in enumerate(checks):
                if not isinstance(c, dict) or not c.get("id"):
                    problems.append(f"checks[{i}] missing `id`")
                elif c.get("status") not in OK_STATUS:
                    problems.append(f"checks[{i}] status {c.get('status')!r} not in {sorted(OK_STATUS)}")
        if not isinstance(evidence.get("examined"), list):
            problems.append("missing or non-list `examined`")
    if problems:
        print(json.dumps({"check": "schema-valid", "pass": False,
                          "feedback": "schema invalid: " + "; ".join(problems[:6])}))
    else:
        print(json.dumps({"check": "schema-valid", "pass": True, "feedback": ""}))


if __name__ == "__main__":
    main()

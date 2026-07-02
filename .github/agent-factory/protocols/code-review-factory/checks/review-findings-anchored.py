#!/usr/bin/env python3
"""Check: every review finding anchors to a real RIGHT-side line in the diff."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diff  # noqa: E402


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        _emit([f"evidence unreadable/not JSON: {exc}"])
        return

    findings = ev.get("findings") if isinstance(ev, dict) else None
    if not isinstance(findings, list):
        _emit(["`findings` missing or not an array"])
        return

    try:
        maps = _diff.parse_diff(sys.argv[2]) if len(sys.argv) > 2 else {}
    except OSError as exc:
        _emit([f"diff unreadable: {exc}"])
        return

    problems = []
    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            problems.append(f"findings[{i}] not an object")
            continue
        path = finding.get("path")
        line = finding.get("line")
        right = (maps.get(path) or {}).get("RIGHT") or {}
        if not isinstance(line, int) or isinstance(line, bool) or line not in right:
            problems.append(f"findings[{i}] line {line!r} not on RIGHT side of {path!r}")
            continue
        start = finding.get("start_line")
        if start is not None:
            if not isinstance(start, int) or isinstance(start, bool):
                problems.append(f"findings[{i}] start_line must be an integer")
                continue
            if start > line:
                problems.append(f"findings[{i}] start_line must be <= line")
                continue
            hunk = right[line][1]
            for n in range(start, line + 1):
                if n not in right or right[n][1] != hunk:
                    problems.append(
                        f"findings[{i}] range {start}..{line} not one RIGHT hunk in {path!r}"
                    )
                    break
        if len(problems) > 8:
            break

    _emit(problems)


def _emit(problems):
    if problems:
        print(
            json.dumps(
                {
                    "check": "review-findings-anchored",
                    "pass": False,
                    "feedback": "unanchored findings: " + "; ".join(problems[:6]),
                }
            )
        )
    else:
        print(
            json.dumps(
                {"check": "review-findings-anchored", "pass": True, "feedback": ""}
            )
        )


if __name__ == "__main__":
    main()

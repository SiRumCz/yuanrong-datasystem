#!/usr/bin/env python3
"""Check: every reviewable changed file × every category has exactly one verdict.

Usage: rubric-coverage.py <evidence.json> <diff.txt> <changed-files.txt>

A polyglot example: this check honours the same ABI as the bash checks
(3 path args in, one {check,pass,feedback} JSON object on stdout, exit 0) but is
written in Python. Ground truth is the changed-files list (arg 3), filtered to
.js; the diff (arg 2) is unused here. Categories come from CHECK_PARAMS
(engine-resolved, scoped to this check's node), never hardcoded.
"""
import json
import os
import sys


def main() -> None:
    ev_path, _diff, files_path = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        categories = json.loads(os.environ.get("CHECK_PARAMS", "")).get("categories")
    except (ValueError, AttributeError):
        categories = None
    if not isinstance(categories, list) or not categories:
        print(json.dumps({
            "check": "rubric-coverage",
            "pass": False,
            "feedback": "rubric-coverage: no categories in CHECK_PARAMS "
                        "(engine must pass params.categories for this check's node)",
        }))
        return

    # Evidence may be missing or malformed — treat as "no verdicts" so this check
    # reports missing cells rather than crashing (schema-valid reports the shape).
    try:
        with open(ev_path) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    files = evidence.get("files", []) if isinstance(evidence, dict) else []

    counts: dict[tuple, int] = {}
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        verdicts = entry.get("verdicts") or []
        if not isinstance(verdicts, list):
            continue
        for verdict in verdicts:
            if isinstance(verdict, dict):
                key = (path, verdict.get("category"))
                counts[key] = counts.get(key, 0) + 1

    with open(files_path) as fh:
        changed = [line.rstrip("\r\n") for line in fh]

    bad = []
    for path in changed:
        if not path.endswith(".js"):
            continue
        for category in categories:
            n = counts.get((path, category), 0)
            if n != 1:
                bad.append(f"{category} × {path} (verdicts: {n})")

    if bad:
        out = {
            "check": "rubric-coverage",
            "pass": False,
            "feedback": "Missing or duplicated rubric cells: " + "; ".join(bad),
        }
    else:
        out = {"check": "rubric-coverage", "pass": True, "feedback": ""}

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()

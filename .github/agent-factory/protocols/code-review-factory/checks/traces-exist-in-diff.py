#!/usr/bin/env python3
"""Check: every finding's anchor (line[/start_line] on a side) resolves to the
claimed snippet in the independently-fetched diff, and every `examined`
identifier appears in that file's diff hunks.

Usage: traces-exist-in-diff.py <evidence.json> <diff.txt> <changed-files.txt>

This replaces the former "snippet appears somewhere in the diff" check: a finding
must now name the exact line(s) it critiques (RIGHT = new-file line numbers,
LEFT = old-file line numbers), and we verify the snippet sits there. Anchors that
pass here are valid GitHub review positions, so the publish hook can post them in
a single review without the all-or-nothing reviews API 422-ing.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _trace  # noqa: E402

verify_finding = _trace.verify_finding
findings_anchor_errors = _trace.findings_anchor_errors


def main():
    if len(sys.argv) < 4:
        print(json.dumps({
            "check": "traces-exist-in-diff",
            "pass": False,
            "feedback": "usage: traces-exist-in-diff.py <evidence.json> <diff.txt> <changed-files.txt>",
        }))
        sys.exit(0)
    # _files (changed-files.txt) is unused: the diff is the source of truth here.
    ev_path, diff_path, _files = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        with open(ev_path) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    bad = findings_anchor_errors(evidence, diff_path)

    if bad:
        out = {
            "check": "traces-exist-in-diff",
            "pass": False,
            "feedback": "Unverifiable claims: " + "; ".join(bad),
        }
    else:
        out = {"check": "traces-exist-in-diff", "pass": True, "feedback": ""}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()

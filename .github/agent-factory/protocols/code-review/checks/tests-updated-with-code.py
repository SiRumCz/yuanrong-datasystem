#!/usr/bin/env python3
"""Check (advisory): if code files changed, tests should change too.
Usage: tests-updated-with-code.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402


def main():
    files_arg = sys.argv[3] if len(sys.argv) > 3 else ""
    files = _paths.read_changed_files(files_arg)
    code = [f for f in files if _paths.is_code(f)]
    tests = [f for f in files if _paths.is_test(f)]
    if not code:
        print(json.dumps({"check": "tests-updated-with-code", "pass": True,
                          "feedback": "No code files changed; test-coherence not applicable."}))
    elif tests:
        print(json.dumps({"check": "tests-updated-with-code", "pass": True,
                          "feedback": f"Tests updated alongside code ({len(tests)} test file(s))."}))
    else:
        print(json.dumps({"check": "tests-updated-with-code", "pass": False,
                          "feedback": f"Code changed ({len(code)} file(s)) but no tests were added or updated."}))


if __name__ == "__main__":
    main()

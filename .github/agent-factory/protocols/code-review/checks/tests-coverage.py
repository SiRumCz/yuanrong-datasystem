#!/usr/bin/env python3
"""Check: the tests-updated-appropriately leg's evidence is well-formed (N/A when no code
changed). Form only, never substance. See _coherence.evaluate.
Usage: tests-coverage.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _coherence  # noqa: E402
import _paths  # noqa: E402


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        ev = {}
    files = _paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else "")
    print(json.dumps(_coherence.evaluate("tests-coverage", ev, files,
          is_kind=_paths.is_test, kind_label="test", applicable_without_code=False)))


if __name__ == "__main__":
    main()

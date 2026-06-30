#!/usr/bin/env python3
"""Check: the docs-updated-appropriately leg's evidence is well-formed (docs are ALWAYS
applicable — no N/A). Form only, never substance. See _coherence.evaluate.
Usage: docs-coverage.py <evidence.json> <diff.txt> <changed-files.txt>"""
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
    print(json.dumps(_coherence.evaluate("docs-coverage", ev, files,
          is_kind=_paths.is_doc, kind_label="doc", applicable_without_code=True)))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Check: a spec/requirements FILE is present in the PR diff (changed-files).
Changed-files-only (no PR body). on_fail: block — absence blocks the pipeline.
Usage: spec-present.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402

SEARCHED = "docs/specs/, docs/superpowers/specs/, specs/, SPEC.md, REQUIREMENTS.md"


def main():
    files_arg = sys.argv[3] if len(sys.argv) > 3 else ""
    files = _paths.read_changed_files(files_arg)
    hits = [f for f in files if _paths.is_spec_path(f)]
    if hits:
        print(json.dumps({"check": "spec-present", "pass": True,
                          "feedback": f"Spec artifact in diff: {hits[0]}"}))
    else:
        print(json.dumps({"check": "spec-present", "pass": False,
                          "feedback": f"No spec/requirements file in the PR diff (searched: {SEARCHED})."}))


if __name__ == "__main__":
    main()

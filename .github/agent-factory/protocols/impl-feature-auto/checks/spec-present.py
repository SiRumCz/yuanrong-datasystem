#!/usr/bin/env python3
"""spec-present (block) — the bundled spec.md exists and carries the 5 required
sections. Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

REQUIRED = ["summary", "scope", "behavior", "accountability ledger", "read these first"]


def emit(ok, feedback):
    print(json.dumps({"check": "spec-present", "pass": ok, "feedback": feedback}))


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    spec_path = _common.sibling(ev_path, "spec.md")
    if not spec_path:
        emit(False, "no spec.md bundled beside evidence.json (design must write + upload the spec)")
        return
    try:
        text = open(spec_path, encoding="utf-8", errors="replace").read().lower()
    except OSError as e:
        emit(False, f"unable to read spec.md: {e}")
        return
    # consider only markdown heading lines for section matching
    headings = "\n".join(ln for ln in text.splitlines() if ln.lstrip().startswith("#"))
    missing = [s for s in REQUIRED if s not in headings]
    if missing:
        emit(False, f"spec.md missing required section(s): {', '.join(missing)}")
    else:
        emit(True, "")


if __name__ == "__main__":
    main()

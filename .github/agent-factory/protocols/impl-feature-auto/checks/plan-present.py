#!/usr/bin/env python3
"""plan-present (block) — evidence.plan_path is set and a bundled plan.md exists
and is non-empty. Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402


def emit(ok, feedback):
    print(json.dumps({"check": "plan-present", "pass": ok, "feedback": feedback}))


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    ev = _common.load_evidence(ev_path)
    if not _common.NON_TRIVIAL(ev.get("plan_path")):
        emit(False, "evidence.plan_path missing/trivial (writing-plans produced no plan)")
        return
    plan_path = _common.sibling(ev_path, "plan.md")
    try:
        size = os.path.getsize(plan_path) if plan_path else 0
    except OSError:
        size = 0
    if not plan_path or size == 0:
        emit(False, "no non-empty plan.md bundled beside evidence.json")
        return
    emit(True, "")


if __name__ == "__main__":
    main()

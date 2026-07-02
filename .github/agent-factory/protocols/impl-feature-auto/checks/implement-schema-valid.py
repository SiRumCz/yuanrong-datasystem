#!/usr/bin/env python3
"""implement-schema-valid — the implement node's only check: evidence carries a
non-trivial summary and a pr_branch of the form impl-feature-auto/issue-<N> (so
post-summary can resolve the PR). Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

BRANCH_RE = re.compile(r"^impl-feature-auto/issue-[0-9]+$")


def emit(ok, feedback):
    print(json.dumps({"check": "implement-schema-valid", "pass": ok, "feedback": feedback}))


def main():
    ev = _common.load_evidence(sys.argv[1] if len(sys.argv) > 1 else "")
    problems = []
    if not _common.NON_TRIVIAL(ev.get("summary")):
        problems.append("summary missing/trivial")
    pr_branch = ev.get("pr_branch", "")
    if not isinstance(pr_branch, str) or not BRANCH_RE.match(pr_branch):
        problems.append(f"pr_branch {pr_branch!r} not of form impl-feature-auto/issue-<N>")
    if problems:
        emit(False, "; ".join(problems))
    else:
        emit(True, "")


if __name__ == "__main__":
    main()

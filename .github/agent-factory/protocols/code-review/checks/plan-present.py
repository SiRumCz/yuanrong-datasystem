#!/usr/bin/env python3
"""Check: an implementation-plan artifact is ASSOCIATED with the PR — a changed
plan file, a plan heading in the PR body, or a markdown task checklist in the
body. Ports custody checks.js planPresent + locate.js. Advisory (protocol.json
wires on_fail: advisory): absence never blocks. Unlike spec, plan has NO
PR-description fallback — a description is a claim, not an implementation plan.
Usage: plan-present.py <evidence.json> <diff.txt> <changed-files.txt>; reads PR_BODY env."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _locate  # noqa: E402
import _paths  # noqa: E402

SEARCHED = "PR body, the PR diff, docs/plans/, docs/superpowers/plans/, plans/, PLAN.md"


def main():
    body = os.environ.get("PR_BODY", "") or ""
    files = _paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else "")
    r = _locate.locate("plan", body, files)
    if r["found"]:
        if r["source"] == "body-section":
            feedback = f"Implementation-plan signal in PR body: {r['body_hit']}"
        else:
            feedback = f"Plan artifact in diff: {r['changed_hits'][0]}"
        print(json.dumps({"check": "plan-present", "pass": True, "feedback": feedback}))
    else:
        print(json.dumps({"check": "plan-present", "pass": False,
                          "feedback": f"No implementation-plan artifact associated with this PR (searched: {SEARCHED})."}))


if __name__ == "__main__":
    main()

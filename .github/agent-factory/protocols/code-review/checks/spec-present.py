#!/usr/bin/env python3
"""Check: a spec/requirements artifact is ASSOCIATED with the PR — a changed
spec/requirements file, a requirements section in the PR body, or (fallback) the
PR description itself used as the claim. Ports custody checks.js specPresent +
locate.js. Advisory (protocol.json wires on_fail: advisory): absence never blocks
the gate; it only flags that adherence cannot be verified.
Usage: spec-present.py <evidence.json> <diff.txt> <changed-files.txt>; reads PR_BODY env."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _locate  # noqa: E402
import _paths  # noqa: E402

SEARCHED = "PR body, the PR diff, docs/specs/, docs/superpowers/specs/, specs/, SPEC.md, REQUIREMENTS.md"


def main():
    body = os.environ.get("PR_BODY", "") or ""
    files = _paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else "")
    r = _locate.locate("spec", body, files)
    if r["found"]:
        if r["source"] == "pr-description":
            feedback = "No committed spec file or requirements section — judging against the PR description as the requirements/claim."
        elif r["source"] == "body-section":
            feedback = f"Spec/requirements section in PR body: {r['body_hit']}"
        else:
            feedback = f"Spec artifact in diff: {r['changed_hits'][0]}"
        print(json.dumps({"check": "spec-present", "pass": True, "feedback": feedback}))
    else:
        print(json.dumps({"check": "spec-present", "pass": False,
                          "feedback": f"No spec/requirements artifact associated with this PR (searched: {SEARCHED})."}))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Check: the agent judged exactly the adherence checks that the PR's committed
artifacts call for — spec file in diff ⇒ spec-adherence judged once; plan file ⇒
plan-adherence; absent ⇒ that check must NOT appear (it was correctly scoped out).
Expected set is derived from changed-files (NOT from agent output), so zone 3 stays
independent. Usage: adherence-coverage.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402

# Which ai_check id maps to which artifact presence.
ARTIFACT_OF = {"spec-adherence": _paths.is_spec_path, "plan-adherence": _paths.is_plan_path}


def main():
    try:
        ai_checks = json.loads(os.environ.get("CHECK_PARAMS", "")).get("ai_checks")
    except (ValueError, AttributeError):
        ai_checks = None
    if not isinstance(ai_checks, list) or not ai_checks:
        print(json.dumps({"check": "adherence-coverage", "pass": False,
                          "feedback": "no ai_checks in CHECK_PARAMS (engine must pass params.ai_checks)"}))
        return

    files = _paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else "")
    expected = set()
    for cid in ai_checks:
        matcher = ARTIFACT_OF.get(cid)
        if matcher and any(matcher(f) for f in files):
            expected.add(cid)

    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    judged = []
    if isinstance(evidence, dict):
        for c in evidence.get("checks", []) or []:
            if isinstance(c, dict) and c.get("id"):
                judged.append(c["id"])
    judged_set = set(judged)

    missing = expected - judged_set
    unexpected = (judged_set & set(ai_checks)) - expected
    dups = sorted({c for c in judged if judged.count(c) > 1})
    problems = []
    if missing:    problems.append(f"missing verdict(s): {sorted(missing)}")
    if unexpected: problems.append(f"unexpected verdict(s) (no artifact in diff): {sorted(unexpected)}")
    if dups:       problems.append(f"duplicate verdict(s): {dups}")
    if problems:
        print(json.dumps({"check": "adherence-coverage", "pass": False,
                          "feedback": "adherence coverage off: " + "; ".join(problems)}))
    else:
        print(json.dumps({"check": "adherence-coverage", "pass": True,
                          "feedback": f"adherence coverage complete (expected {sorted(expected)})."}))


if __name__ == "__main__":
    main()

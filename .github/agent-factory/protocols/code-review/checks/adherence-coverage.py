#!/usr/bin/env python3
"""Check: the agent judged exactly the adherence checks that the PR's associated
artifacts call for — a spec associated with the PR ⇒ spec-adherence judged once;
a plan associated ⇒ plan-adherence; not associated ⇒ that check must NOT appear
(it was correctly scoped out). "Associated" mirrors the preflight-agent prefetch
scoping via the shared _locate locator (a changed spec/plan file, a body section,
or — spec only — the PR description as the claim), NOT changed-files alone, so a
description-sourced spec is judged AND covered. Expected set is derived from the
PR body + changed-files (NOT from agent output), so the check stays independent
of what the agent reported.
Usage: adherence-coverage.py <evidence.json> <diff.txt> <changed-files.txt>; reads PR_BODY env."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _locate  # noqa: E402
import _paths  # noqa: E402

# Which ai_check id maps to which artifact kind for the shared locator.
ARTIFACT_KIND = {"spec-adherence": "spec", "plan-adherence": "plan"}


def main():
    try:
        ai_checks = json.loads(os.environ.get("CHECK_PARAMS", "")).get("ai_checks")
    except (ValueError, AttributeError):
        ai_checks = None
    if not isinstance(ai_checks, list) or not ai_checks:
        print(json.dumps({"check": "adherence-coverage", "pass": False,
                          "feedback": "no ai_checks in CHECK_PARAMS (engine must pass params.ai_checks)"}))
        return

    body = os.environ.get("PR_BODY", "") or ""
    files = _paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else "")
    expected = set()
    for cid in ai_checks:
        kind = ARTIFACT_KIND.get(cid)
        if kind and _locate.locate(kind, body, files)["found"]:
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

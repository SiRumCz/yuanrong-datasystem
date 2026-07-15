#!/usr/bin/env python3
"""publish-review.py files one labeled GitHub issue per finding.

The review publish hook no longer posts a PR review with inline comments; it
opens one issue per finding, labeled `ai-review` + `review:<dim>`, titled
`[ai-review][<dim>] <finding-title>`. The downstream conclude-triage /
conclude-fix hooks link and close those issues by exactly that title+label — so
this test pins the issue-plan contract (title / labels / body / cap), not a
review-comment payload.

In ENGINE_LOCAL=1 dry-run the hook writes the would-be plan (a list of
{title, labels, body}) to $REVIEW_ISSUES_OUT instead of calling the API, and
prints {"conclusion","summary"} to stdout.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "publish", "publish-review.py")
failures = []


def run(evidence):
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "e.json")
    open(ev, "w").write(json.dumps(evidence))
    out = os.path.join(d, "issues.json")
    env = {
        **os.environ,
        "ENGINE_LOCAL": "1",
        "GITHUB_REPOSITORY": "o/r",
        "PR": "5",
        "REVIEW_ISSUES_OUT": out,
    }
    r = subprocess.run([HOOK, ev, "pr-5"], text=True, capture_output=True, env=env)
    verdict = json.loads(r.stdout.strip())
    plan = json.load(open(out)) if os.path.isfile(out) else None
    return verdict, plan


def ok(n, c):
    if not c:
        failures.append(n)


# REQUEST_CHANGES => one issue, failure conclusion, well-formed title/labels/body
REQ = {
    "dimension": "correctness",
    "verdict": "REQUEST_CHANGES",
    "findings": [
        {
            "path": "a.cpp",
            "line": 5,
            "severity": "high",
            "category": "correctness",
            "title": "bug",
            "impact": "boom",
            "fix": "guard",
        }
    ],
}
v, plan = run(REQ)
ok("conclusion failure", v["conclusion"] == "failure")
ok("one issue planned", len(plan) == 1)
# title MUST be "[ai-review][<dim>] <title>" so conclude-fix._close_issues matches
ok("issue title prefixed with [ai-review][dim]",
   plan[0]["title"] == "[ai-review][correctness] bug")
ok("issue carries ai-review + review:<dim> labels",
   plan[0]["labels"] == ["ai-review", "review:correctness"])
body = plan[0]["body"]
ok("body anchors path:line", "`a.cpp:5`" in body)
ok("body carries severity", "**high**" in body)
ok("body carries impact", "boom" in body)
ok("body carries suggested fix", "guard" in body)

# APPROVE + no findings => no issues, success conclusion
v, plan = run({"dimension": "test", "verdict": "APPROVE", "findings": []})
ok("approve -> success conclusion", v["conclusion"] == "success")
ok("approve -> no issues planned", plan == [])

# COMMENT verdict (non-critical) => neutral conclusion, still files the finding
COMMENT_EV = {
    "dimension": "maintainability",
    "verdict": "COMMENT",
    "findings": [
        {
            "path": "b.py",
            "line": 3,
            "severity": "low",
            "category": "maintainability",
            "title": "style nit",
            "impact": "minor",
            "fix": "rename var",
        }
    ],
}
vc, pc = run(COMMENT_EV)
ok("comment -> neutral conclusion", vc["conclusion"] == "neutral")
ok("comment verdict still files an issue", len(pc) == 1)
ok("comment issue title/labels scoped to its dim",
   pc[0]["title"] == "[ai-review][maintainability] style nit"
   and pc[0]["labels"] == ["ai-review", "review:maintainability"])

# at most 5 issues per dimension (findings[:5])
MANY = {
    "dimension": "correctness",
    "verdict": "REQUEST_CHANGES",
    "findings": [
        {"title": f"f{i}", "path": "x.c", "line": i, "severity": "low",
         "impact": "i", "fix": "f"}
        for i in range(6)
    ],
}
vm, pm = run(MANY)
ok("issue plan capped at 5 per dimension", len(pm) == 5)

if failures:
    print("FAIL test_publish_review:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - publish-review issue plan (title/label/body/cap) + verdict conclusion")

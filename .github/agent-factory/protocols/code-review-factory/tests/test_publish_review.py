#!/usr/bin/env python3
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
    out = os.path.join(d, "post.json")
    env = {
        **os.environ,
        "ENGINE_LOCAL": "1",
        "GITHUB_REPOSITORY": "o/r",
        "PR": "5",
        "HEAD_SHA": "abc",
        "REVIEW_POST_OUT": out,
    }
    r = subprocess.run([HOOK, ev, "pr-5"], text=True, capture_output=True, env=env)
    verdict = json.loads(r.stdout.strip())
    payload = json.load(open(out)) if os.path.isfile(out) else None
    return verdict, payload


def ok(n, c):
    if not c:
        failures.append(n)


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
v, p = run(REQ)
ok("request_changes event", p["event"] == "REQUEST_CHANGES")
ok("one comment", len(p["comments"]) == 1)
ok(
    "comment anchored RIGHT",
    p["comments"][0]["side"] == "RIGHT" and p["comments"][0]["line"] == 5,
)
ok("conclusion failure", v["conclusion"] == "failure")

v, p = run({"dimension": "test", "verdict": "APPROVE", "findings": []})
ok("approve event", p["event"] == "APPROVE")
ok("no comments", p["comments"] == [])
ok("conclusion success", v["conclusion"] in ("success", "neutral"))

# COMMENT verdict (non-critical finding, verdict != APPROVE/REQUEST_CHANGES)
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
ok("comment event", pc["event"] == "COMMENT")
ok("comment conclusion neutral", vc["conclusion"] == "neutral")

# comment body contains severity marker and <details> disclosure
body = pc["comments"][0]["body"]
ok("comment body has severity marker", "[low]" in body)
ok("comment body has details tag", "<details>" in body)

# finding WITH start_line => comment carries start_line and start_side
WITH_RANGE = {
    "dimension": "correctness",
    "verdict": "REQUEST_CHANGES",
    "findings": [
        {
            "path": "c.cpp",
            "line": 8,
            "start_line": 5,
            "severity": "high",
            "category": "correctness",
            "title": "range bug",
            "impact": "crash",
            "fix": "add guard",
        }
    ],
}
vr, pr = run(WITH_RANGE)
ok("start_line propagated to comment", pr["comments"][0].get("start_line") == 5)
ok("start_side propagated to comment", pr["comments"][0].get("start_side") == "RIGHT")

if failures:
    print("FAIL test_publish_review:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - publish-review payload + verdict")

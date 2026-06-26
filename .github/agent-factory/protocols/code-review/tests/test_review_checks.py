#!/usr/bin/env python3
"""ABI tests for review-schema-valid.py and review-findings-anchored.py."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = os.path.join(HERE, "..", "checks")
failures = []


def run(check, evidence, diff="", changed="", params=None):
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "e.json")
    open(ev, "w").write(json.dumps(evidence))
    df = os.path.join(d, "d.txt")
    open(df, "w").write(diff)
    cf = os.path.join(d, "c.txt")
    open(cf, "w").write(changed)
    env = {**os.environ, "CHECK_PARAMS": json.dumps(params or {})}
    r = subprocess.run(
        [sys.executable, os.path.join(CHECKS, check), ev, df, cf],
        text=True,
        capture_output=True,
        env=env,
    )
    return json.loads(r.stdout.strip())


def ok(n, c):
    if not c:
        failures.append(n)


OKREV = {
    "dimension": "correctness",
    "verdict": "REQUEST_CHANGES",
    "findings": [
        {
            "path": "a.cpp",
            "line": 5,
            "severity": "high",
            "category": "correctness",
            "title": "t",
            "impact": "i",
            "fix": "f",
        }
    ],
}
P = {"dimension": "correctness"}

ok("valid passes", run("review-schema-valid.py", OKREV, params=P)["pass"] is True)
ok(
    "bad verdict fails",
    run("review-schema-valid.py", {**OKREV, "verdict": "MAYBE"}, params=P)[
        "pass"
    ]
    is False,
)
bad_cat = json.loads(json.dumps(OKREV))
bad_cat["findings"][0]["category"] = "security"
ok(
    "category!=dimension fails",
    run("review-schema-valid.py", bad_cat, params=P)["pass"] is False,
)
ok(
    "dimension mismatch fails",
    run("review-schema-valid.py", OKREV, params={"dimension": "security"})["pass"]
    is False,
)
ok(
    "approve with findings fails",
    run(
        "review-schema-valid.py",
        {"dimension": "correctness", "verdict": "APPROVE", "findings": OKREV["findings"]},
        params=P,
    )["pass"]
    is False,
)
ok(
    "high without REQUEST_CHANGES fails",
    run("review-schema-valid.py", {**OKREV, "verdict": "COMMENT"}, params=P)[
        "pass"
    ]
    is False,
)
ok(
    "approve empty passes",
    run(
        "review-schema-valid.py",
        {"dimension": "correctness", "verdict": "APPROVE", "findings": []},
        params=P,
    )["pass"]
    is True,
)

DIFF = """diff --git a/a.cpp b/a.cpp
--- a/a.cpp
+++ b/a.cpp
@@ -1,1 +1,2 @@
 ctx
+changed
"""

ok(
    "anchored ok",
    run(
        "review-findings-anchored.py",
        {
            "dimension": "correctness",
            "verdict": "REQUEST_CHANGES",
            "findings": [
                {
                    "path": "a.cpp",
                    "line": 2,
                    "severity": "high",
                    "category": "correctness",
                    "title": "t",
                    "impact": "i",
                    "fix": "f",
                }
            ],
        },
        diff=DIFF,
    )["pass"]
    is True,
)
ok(
    "unanchored fails",
    run(
        "review-findings-anchored.py",
        {
            "dimension": "correctness",
            "verdict": "REQUEST_CHANGES",
            "findings": [
                {
                    "path": "a.cpp",
                    "line": 99,
                    "severity": "high",
                    "category": "correctness",
                    "title": "t",
                    "impact": "i",
                    "fix": "f",
                }
            ],
        },
        diff=DIFF,
    )["pass"]
    is False,
)
ok(
    "empty findings passes",
    run(
        "review-findings-anchored.py",
        {"dimension": "correctness", "verdict": "APPROVE", "findings": []},
        diff=DIFF,
    )["pass"]
    is True,
)

# review-schema-valid: line:0 is not >= 1 so must fail
ok(
    "line 0 fails schema",
    run(
        "review-schema-valid.py",
        {
            **OKREV,
            "findings": [
                {
                    "path": "a.cpp",
                    "line": 0,
                    "severity": "high",
                    "category": "correctness",
                    "title": "t",
                    "impact": "i",
                    "fix": "f",
                }
            ],
        },
        params=P,
    )["pass"]
    is False,
)

# review-schema-valid: start_line > line must fail
ok(
    "start_line > line fails schema",
    run(
        "review-schema-valid.py",
        {
            **OKREV,
            "findings": [
                {
                    "path": "a.cpp",
                    "line": 5,
                    "start_line": 10,
                    "severity": "high",
                    "category": "correctness",
                    "title": "t",
                    "impact": "i",
                    "fix": "f",
                }
            ],
        },
        params=P,
    )["pass"]
    is False,
)

# review-schema-valid: valid start_line <= line must pass
ok(
    "valid start_line <= line passes schema",
    run(
        "review-schema-valid.py",
        {
            **OKREV,
            "findings": [
                {
                    "path": "a.cpp",
                    "line": 5,
                    "start_line": 3,
                    "severity": "high",
                    "category": "correctness",
                    "title": "t",
                    "impact": "i",
                    "fix": "f",
                }
            ],
        },
        params=P,
    )["pass"]
    is True,
)

# 2-hunk diff for review-findings-anchored
# Hunk 1: RIGHT lines 1-3; Hunk 2: RIGHT lines 10-12
DIFF2 = """diff --git a/b.cpp b/b.cpp
--- a/b.cpp
+++ b/b.cpp
@@ -1,2 +1,3 @@
 ctx1
+added1
 ctx2
@@ -10,2 +10,3 @@
 ctx3
+added2
 ctx4
"""

FIND_BASE = {
    "dimension": "correctness",
    "verdict": "REQUEST_CHANGES",
    "severity": "high",
    "category": "correctness",
    "title": "t",
    "impact": "i",
    "fix": "f",
}


def _finding(**kw):
    return {**FIND_BASE, **kw}


# Case A: start_line..line fully within hunk 1 (lines 1-3 are all in hunk 0)
ok(
    "anchored range within single hunk passes",
    run(
        "review-findings-anchored.py",
        {
            "dimension": "correctness",
            "verdict": "REQUEST_CHANGES",
            "findings": [_finding(path="b.cpp", line=2, start_line=1)],
        },
        diff=DIFF2,
    )["pass"]
    is True,
)

# Case B: the start_line..line range spans the gap between the two hunks, so it
# includes new-file line(s) that are not RIGHT-changed lines => rejected. (Within a
# hunk RIGHT line numbers are contiguous and they are gapped between hunks, so a
# multi-hunk range can never be gap-free; the membership check is what catches it.)
ok(
    "anchored range crossing hunks fails",
    run(
        "review-findings-anchored.py",
        {
            "dimension": "correctness",
            "verdict": "REQUEST_CHANGES",
            "findings": [_finding(path="b.cpp", line=11, start_line=2)],
        },
        diff=DIFF2,
    )["pass"]
    is False,
)

if failures:
    print("FAIL test_review_checks:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - review-schema-valid + review-findings-anchored")

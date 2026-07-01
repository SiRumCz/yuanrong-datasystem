#!/usr/bin/env python3
"""ABI tests for conclude-triage.py authoritative gate derivation."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "publish", "conclude-triage.py")
failures = []


def run(evidence, inputs):
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "e.json")
    open(ev, "w").write(json.dumps(evidence))
    inputs_dir = os.path.join(d, "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    for name, obj in inputs.items():
        with open(os.path.join(inputs_dir, f"{name}.json"), "w") as fh:
            json.dump(obj, fh)
    triage_out = os.path.join(d, "triage.json")
    comment_out = os.path.join(d, "comment.txt")
    env = {
        **os.environ,
        "ENGINE_LOCAL": "1",
        "CONCLUDE_INPUTS_DIR": inputs_dir,
        "TRIAGE_OUT": triage_out,
        "TRIAGE_COMMENT_OUT": comment_out,
        "GITHUB_REPOSITORY": "o/r",
        "PR": "7",
        "HEAD_SHA": "abc",
    }
    r = subprocess.run([HOOK, ev, "pr-7"], text=True, capture_output=True, env=env)
    verdict = json.loads(r.stdout.strip())
    out = json.load(open(triage_out))
    comment = open(comment_out).read() if os.path.isfile(comment_out) else ""
    return verdict, out, comment


def ok(n, c):
    if not c:
        failures.append(n)


HIGH_FINDING = {
    "path": "a.cpp",
    "line": 12,
    "severity": "high",
    "category": "correctness",
    "title": "bug",
    "impact": "boom",
    "fix": "guard",
}
REVIEW = {
    "dimension": "correctness",
    "verdict": "REQUEST_CHANGES",
    "findings": [HIGH_FINDING],
}
TRIAGE = {
    "clusters": [
        {
            "cluster_id": "c1",
            "title": "real cluster",
            "dimension": ["correctness"],
            "severity": "high",
            "paths": ["a.cpp"],
            "member_findings": [{**HIGH_FINDING, "dimension": "correctness"}],
            "rank": 1,
        },
        {
            "cluster_id": "c2",
            "title": "fabricated cluster",
            "dimension": ["security"],
            "severity": "medium",
            "paths": ["x.cpp"],
            "member_findings": [
                {
                    "dimension": "security",
                    "path": "x.cpp",
                    "line": 99,
                    "severity": "medium",
                    "title": "made up",
                }
            ],
            "rank": 2,
        },
    ],
    "summary": {
        "present": [],
        "missing": ["correctness", "test", "performance", "security", "maintainability"],
        "clusters": 0,
        "total_findings": 0,
        "by_severity": {},
        "by_dimension": {},
    },
}

v, out, comment = run(TRIAGE, {"correctness": REVIEW})
ok("blocked false", v["blocked"] is False)
ok("conclusion failure", v["conclusion"] == "failure")
ok("gate request-changes", out["gate"]["verdict"] == "request-changes")
ok("authoritative cluster by_severity", out["summary"]["by_severity"] == {"high": 1, "medium": 1})
ok("authoritative present", out["summary"]["present"] == ["correctness"])
ok("fabricated flagged", len(out["fabricated"]) == 1)
ok("comment mentions gate", "request-changes" in comment)

v, out, _ = run({"clusters": [], "summary": {}}, {})
ok("no inputs incomplete", out["gate"]["verdict"] == "incomplete")
ok("no inputs neutral", v["conclusion"] == "neutral")

# Degraded path: clusters present but NO review inputs to compare against. We cannot
# verify membership, so flag nothing — must not spuriously mark every member fabricated.
v, out, _ = run(TRIAGE, {})
ok("no-inputs: no spurious fabrication", out["fabricated"] == [])

if failures:
    print("FAIL test_conclude_triage:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - conclude-triage authoritative gate + fabricated members")

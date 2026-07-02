#!/usr/bin/env python3
"""ABI tests for conclude-fix.py completeness and suggestion comments."""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "publish", "conclude-fix.py")
failures = []


def run(evidence, triage):
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "e.json")
    open(ev, "w").write(json.dumps(evidence))
    inputs_dir = os.path.join(d, "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    with open(os.path.join(inputs_dir, "triage.json"), "w") as fh:
        json.dump(triage, fh)
    fix_out = os.path.join(d, "fix.json")
    review_out = os.path.join(d, "review.json")
    env = {
        **os.environ,
        "ENGINE_LOCAL": "1",
        "CONCLUDE_INPUTS_DIR": inputs_dir,
        "FIX_OUT": fix_out,
        "FIX_REVIEW_OUT": review_out,
        "GITHUB_REPOSITORY": "o/r",
        "PR": "9",
        "HEAD_SHA": "abc",
    }
    r = subprocess.run([HOOK, ev, "pr-9"], text=True, capture_output=True, env=env)
    verdict = json.loads(r.stdout.strip())
    out = json.load(open(fix_out))
    payload = json.load(open(review_out)) if os.path.isfile(review_out) else None
    return verdict, out, payload


def ok(n, c):
    if not c:
        failures.append(n)


TRIAGE = {
    "clusters": [
        {"cluster_id": "c1", "dimension": ["correctness"], "title": "bug", "rank": 1},
        {"cluster_id": "c2", "dimension": ["security"], "title": "leak", "rank": 2},
        {"cluster_id": "c3", "dimension": ["test"], "title": "missing test", "rank": 3},
    ],
    "summary": {},
}
FIX = {
    "mode": "suggest",
    "fixes": [
        {
            "cluster_id": "c1",
            "path": "a.cpp",
            "line": 10,
            "rationale": "guard the nil case",
            "suggested_patch": "if (!p) return;",
        }
    ],
    "skipped": [{"cluster_id": "c2", "reason": "needs larger refactor"}],
}

v, out, payload = run(FIX, TRIAGE)
ok("blocked false", v["blocked"] is False)
ok("neutral conclusion", v["conclusion"] == "neutral")
ok("applied c1", out["applied"] == ["c1"])
ok("skipped c2", out["skipped"] == ["c2"])
ok("test-only excluded", out["dropped"] == [])
ok("comment review", payload["event"] == "COMMENT")
ok("one suggestion", len(payload["comments"]) == 1)
ok("suggestion body", "```suggestion" in payload["comments"][0]["body"])

triage_drop = copy.deepcopy(TRIAGE)
triage_drop["clusters"].append(
    {"cluster_id": "c4", "dimension": ["maintainability"], "title": "cleanup", "rank": 4}
)
v, out, _ = run(FIX, triage_drop)
ok("dropped c4", out["dropped"] == ["c4"])

# Anti-fabrication: a fix/skipped cluster_id absent from triage lands in the unknown
# bucket (the fix-phase analogue of triage's fabricated-member guard) and is surfaced.
fab = copy.deepcopy(FIX)
fab["fixes"].append(
    {"cluster_id": "zzz", "path": "z.cpp", "line": 1, "rationale": "r", "suggested_patch": "x"}
)
fab["skipped"].append({"cluster_id": "yyy", "reason": "r"})
v, out, _ = run(fab, TRIAGE)
ok("unknown fixes zzz", out["unknown"]["fixes"] == ["zzz"])
ok("unknown skipped yyy", out["unknown"]["skipped"] == ["yyy"])
ok("unknown surfaced in summary", "unknown=2" in v["summary"])

if failures:
    print("FAIL test_conclude_fix:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - conclude-fix completeness + suggestion comments")

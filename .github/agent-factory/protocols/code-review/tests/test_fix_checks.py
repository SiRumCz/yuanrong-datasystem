#!/usr/bin/env python3
"""ABI tests for fix-schema-valid.py (code-review: diff-shape evidence)."""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "..", "checks", "fix-schema-valid.py")
failures = []

SAMPLE_DIFF = (
    "diff --git a/a.cpp b/a.cpp\n"
    "--- a/a.cpp\n"
    "+++ b/a.cpp\n"
    "@@ -1,1 +1,1 @@\n"
    "-if (p) return;\n"
    "+if (!p) return;\n"
)


def run(evidence):
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "e.json")
    open(ev, "w").write(json.dumps(evidence))
    df = os.path.join(d, "d.txt")
    open(df, "w").write("")
    cf = os.path.join(d, "c.txt")
    open(cf, "w").write("")
    r = subprocess.run([sys.executable, CHECK, ev, df, cf], text=True, capture_output=True)
    return json.loads(r.stdout.strip())


def ok(n, c):
    if not c:
        failures.append(n)


VALID = {
    "fixes": [
        {
            "cluster_id": "c1",
            "diff": SAMPLE_DIFF,
        }
    ],
    "skipped": [{"cluster_id": "c2", "reason": "needs larger refactor"}],
    "mode": "edit",
}

ok("valid passes", run(VALID)["pass"] is True)

empty_diff = copy.deepcopy(VALID)
empty_diff["fixes"][0]["diff"] = ""
ok("empty diff fails", run(empty_diff)["pass"] is False)

missing_diff = copy.deepcopy(VALID)
del missing_diff["fixes"][0]["diff"]
ok("missing diff fails", run(missing_diff)["pass"] is False)

missing_cluster_id = copy.deepcopy(VALID)
del missing_cluster_id["fixes"][0]["cluster_id"]
ok("missing cluster_id fails", run(missing_cluster_id)["pass"] is False)

bad_mode = copy.deepcopy(VALID)
bad_mode["mode"] = "suggest"
ok("old 'suggest' mode fails", run(bad_mode)["pass"] is False)

both = copy.deepcopy(VALID)
both["skipped"].append({"cluster_id": "c1", "reason": "also skipped"})
ok("cluster in fixes and skipped fails", run(both)["pass"] is False)

# skipped[] entry missing required field (reason) => pass False
missing_reason = copy.deepcopy(VALID)
del missing_reason["skipped"][0]["reason"]
ok("missing skipped reason fails", run(missing_reason)["pass"] is False)

# same cluster_id in both fixes[] and skipped[] (fresh evidence, not inherited) => pass False
overlap = {
    "mode": "edit",
    "fixes": [
        {
            "cluster_id": "dup",
            "diff": SAMPLE_DIFF,
        }
    ],
    "skipped": [{"cluster_id": "dup", "reason": "also skip"}],
}
ok("same cluster_id in fixes and skipped fails", run(overlap)["pass"] is False)

if failures:
    print("FAIL test_fix_checks:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - fix-schema-valid")

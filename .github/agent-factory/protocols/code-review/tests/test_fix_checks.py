#!/usr/bin/env python3
"""ABI tests for fix-schema-valid.py."""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "..", "checks", "fix-schema-valid.py")
failures = []


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
            "path": "a.cpp",
            "line": 10,
            "rationale": "guard the nil case",
            "suggested_patch": "if (!p) return;",
        }
    ],
    "skipped": [{"cluster_id": "c2", "reason": "needs larger refactor"}],
    "mode": "suggest",
}

ok("valid passes", run(VALID)["pass"] is True)

empty_patch = copy.deepcopy(VALID)
empty_patch["fixes"][0]["suggested_patch"] = ""
ok("empty suggested_patch fails", run(empty_patch)["pass"] is False)

bad_line = copy.deepcopy(VALID)
bad_line["fixes"][0]["line"] = 0
ok("line 0 fails", run(bad_line)["pass"] is False)

bad_mode = copy.deepcopy(VALID)
bad_mode["mode"] = "push"
ok("bad mode fails", run(bad_mode)["pass"] is False)

both = copy.deepcopy(VALID)
both["skipped"].append({"cluster_id": "c1", "reason": "also skipped"})
ok("cluster in fixes and skipped fails", run(both)["pass"] is False)

# fixes[] entry missing required field (rationale) => pass False
missing_rationale = copy.deepcopy(VALID)
del missing_rationale["fixes"][0]["rationale"]
ok("missing rationale fails", run(missing_rationale)["pass"] is False)

# same cluster_id in both fixes[] and skipped[] (fresh evidence, not inherited) => pass False
overlap = {
    "mode": "suggest",
    "fixes": [
        {
            "cluster_id": "dup",
            "path": "a.cpp",
            "line": 5,
            "rationale": "fix it",
            "suggested_patch": "x=1",
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

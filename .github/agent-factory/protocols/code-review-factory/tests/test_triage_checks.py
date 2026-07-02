#!/usr/bin/env python3
"""ABI tests for triage-schema-valid.py."""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "..", "checks", "triage-schema-valid.py")
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
    "clusters": [
        {
            "cluster_id": "c1",
            "title": "t",
            "dimension": ["correctness"],
            "severity": "high",
            "paths": ["a.cpp"],
            "rank": 1,
            "member_findings": [
                {
                    "dimension": "correctness",
                    "path": "a.cpp",
                    "severity": "high",
                    "title": "x",
                }
            ],
        }
    ],
    "summary": {
        "present": ["correctness"],
        "missing": ["test", "performance", "security", "maintainability"],
        "clusters": 1,
        "total_findings": 1,
        "by_severity": {"high": 1},
        "by_dimension": {"correctness": 1},
    },
}

ok("valid passes", run(VALID)["pass"] is True)

dup = copy.deepcopy(VALID)
dup["clusters"].append(copy.deepcopy(dup["clusters"][0]))
ok("duplicate cluster_id fails", run(dup)["pass"] is False)

bad_total = copy.deepcopy(VALID)
bad_total["summary"]["total_findings"] = 2
ok("bad total_findings fails", run(bad_total)["pass"] is False)

bad_sev = copy.deepcopy(VALID)
bad_sev["clusters"][0]["severity"] = "bad"
ok("bad severity fails", run(bad_sev)["pass"] is False)

bad_by_sev = copy.deepcopy(VALID)
bad_by_sev["summary"]["by_severity"] = {"high": 5}
ok("bad by_severity fails", run(bad_by_sev)["pass"] is False)

bad_partition = copy.deepcopy(VALID)
bad_partition["summary"]["missing"] = ["test"]
ok("bad present/missing partition fails", run(bad_partition)["pass"] is False)

if failures:
    print("FAIL test_triage_checks:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - triage-schema-valid")

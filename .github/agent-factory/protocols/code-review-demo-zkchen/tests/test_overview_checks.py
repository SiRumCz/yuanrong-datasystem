#!/usr/bin/env python3
"""ABI tests for overview-schema-valid.py and cohort-partition-complete.py.
Run: python3 test_overview_checks.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = os.path.join(HERE, "..", "checks")
SCHEMA = os.path.join(CHECKS, "overview-schema-valid.py")
PART = os.path.join(CHECKS, "cohort-partition-complete.py")

failures = []


def run_check(script, evidence, changed_files=None):
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "evidence.json")
    with open(ev, "w") as fh:
        json.dump(evidence, fh)
    diff = os.path.join(d, "diff.txt")
    open(diff, "w").write("")
    files = os.path.join(d, "files.txt")
    open(files, "w").write("\n".join(changed_files or []))
    r = subprocess.run([sys.executable, script, ev, diff, files],
                       text=True, capture_output=True)
    assert r.returncode == 0, f"{script} exited {r.returncode}: {r.stderr}"
    return json.loads(r.stdout.strip())


def check(name, got, want):
    if got != want:
        failures.append(f"{name}: got {got!r}, want {want!r}")


GOOD = {
    "cohorts": [{
        "cohort": "engine", "cohortOrder": 1, "area": "backend",
        "files": ["engine/api.go"],
        "layers": [{"layer": "backend", "order": 1, "area": "backend",
                    "title": "t", "summary": "s", "files": ["engine/api.go"], "diff": "d"}],
        "bcFindings": [{"symbol": "Engine.Run", "kind": "method", "category": "REMOVE_METHOD",
                        "severityClass": "hard-break", "evidence": "removed"}],
    }],
    "summary": "ok", "risk_band": "High",
}

# ---- overview-schema-valid ----
check("schema.good", run_check(SCHEMA, GOOD)["pass"], True)

bad_area = json.loads(json.dumps(GOOD))
bad_area["cohorts"][0]["area"] = "weird"
v = run_check(SCHEMA, bad_area)
check("schema.bad_area.pass", v["pass"], False)
if "area" not in v["feedback"]:
    failures.append(f"schema.bad_area feedback should mention area: {v['feedback']!r}")

bad_order = json.loads(json.dumps(GOOD))
bad_order["cohorts"][0]["cohortOrder"] = 0
check("schema.bad_order", run_check(SCHEMA, bad_order)["pass"], False)

bad_sev = json.loads(json.dumps(GOOD))
bad_sev["cohorts"][0]["bcFindings"][0]["severityClass"] = "meh"
check("schema.bad_sev", run_check(SCHEMA, bad_sev)["pass"], False)

bad_layer = json.loads(json.dumps(GOOD))
bad_layer["cohorts"][0]["layers"][0]["layer"] = "nope"
check("schema.bad_layer", run_check(SCHEMA, bad_layer)["pass"], False)

bad_band = json.loads(json.dumps(GOOD))
bad_band["risk_band"] = "Severe"
check("schema.bad_band", run_check(SCHEMA, bad_band)["pass"], False)

check("schema.empty_cohorts", run_check(SCHEMA, {"cohorts": [], "summary": "x"})["pass"], False)

# ---- cohort-partition-complete ----
check("part.exact", run_check(PART, GOOD, ["engine/api.go"])["pass"], True)

# gap: a changed file not in any cohort
v = run_check(PART, GOOD, ["engine/api.go", "web/app.js"])
check("part.gap.pass", v["pass"], False)
if "web/app.js" not in v["feedback"]:
    failures.append(f"part.gap should name the missing file: {v['feedback']!r}")

# overlap: same file in two cohorts
overlap = {"cohorts": [
    {"cohort": "a", "cohortOrder": 1, "area": "backend", "files": ["x.go"], "layers": [], "bcFindings": []},
    {"cohort": "b", "cohortOrder": 2, "area": "backend", "files": ["x.go"], "layers": [], "bcFindings": []},
], "summary": "s"}
v = run_check(PART, overlap, ["x.go"])
check("part.overlap", v["pass"], False)

# extra: cohort file not in the diff
v = run_check(PART, GOOD, ["other/file.go"])  # api.go is extra, file.go is a gap
check("part.extra_and_gap", v["pass"], False)

# no changed-files list available -> only overlap checked, coverage not failed
check("part.no_changed_list", run_check(PART, GOOD, [])["pass"], True)
check("part.no_changed_list_overlap", run_check(PART, overlap, [])["pass"], False)

if failures:
    print("FAIL (%d):" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK — overview-schema-valid + cohort-partition-complete")

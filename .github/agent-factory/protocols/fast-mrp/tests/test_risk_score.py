#!/usr/bin/env python3
"""Parity tests for _risk_score.py against custody's score.js golden values
(captured from `node backend/component/risk/score.js`). Run: python3 test_risk_score.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "publish"))
import _risk_score as rs  # noqa: E402

FS = {
    "engine/api.go": {"additions": 40, "deletions": 30},
    "engine/util.go": {"additions": 10, "deletions": 2},
    "web/app.js": {"additions": 60, "deletions": 0},
    "docs/readme.md": {"additions": 5, "deletions": 0},
}

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}: got {got!r}, want {want!r}")


def one(files, bc):
    return rs.score([{"cohort": "c", "cohortOrder": 1, "files": files, "bcFindings": bc}], FS)["cohorts"][0]

# Band + exact score parity (golden values from custody score.js)
c = one(["engine/api.go"], [{"severityClass": "hard-break", "category": "REMOVE_METHOD"}])
check("hard_single.band", c["band"], "High")
check("hard_single.score", c["score"], 0.8)
check("hard_single.P", c["changeRisk"], 0.6039)
check("hard_single.churn", c["churn"], 0.5714)

c = one(["engine/api.go", "web/app.js"], [{"severityClass": "hard-break"}])
check("hard_wide.band", c["band"], "Critical")
check("hard_wide.score", c["score"], 0.84)
check("hard_wide.P", c["changeRisk"], 0.688)
check("hard_wide.NS", c["diffusion"]["NS"], 2)
check("hard_wide.entropy", c["diffusion"]["entropy"], 0.9957)
check("hard_wide.churn", c["churn"], 0.7692)

c = one(["engine/util.go"], [{"severityClass": "recoverable-refactor", "category": "RENAME_METHOD"}])
check("recoverable.band", c["band"], "Medium")
check("recoverable.score", c["score"], 0.23)

c = one(["web/app.js"], [])
check("none.band", c["band"], "Low")
check("none.score", c["score"], 0)

# Missing-fileStats tolerance: file absent from stats defaults 0/0, still bands by severity.
c = one(["unknown/missing.go"], [{"severityClass": "hard-break"}])
check("missing_fs.band", c["band"], "High")
check("missing_fs.score", c["score"], 0.72)
check("missing_fs.churn", c["churn"], 0)

# Mixed-severity cohort: hard-break dominates.
c = one(["engine/api.go"], [{"severityClass": "recoverable-refactor"}, {"severityClass": "hard-break"}])
check("mixed.band", c["band"], "High")

# changeRisk monotonic in blast radius.
check("changeRisk_wide", rs.change_risk({"NS": 2, "ND": 2}, 100), 0.688)
check("changeRisk_narrow", rs.change_risk({"NS": 1, "ND": 1}, 10), 0.5461)

# Overall roll-up: worst band, max score, counts tally.
r = rs.score([
    {"cohort": "crit", "cohortOrder": 1, "files": ["engine/api.go", "web/app.js"], "bcFindings": [{"severityClass": "hard-break"}]},
    {"cohort": "hi", "cohortOrder": 2, "files": ["engine/api.go"], "bcFindings": [{"severityClass": "hard-break", "category": "CHANGE_IN_RETURN_TYPE"}]},
    {"cohort": "lo1", "cohortOrder": 3, "files": ["docs/readme.md"], "bcFindings": []},
    {"cohort": "lo2", "cohortOrder": 4, "files": ["web/app.js"], "bcFindings": []},
], FS)
check("overall.band", r["overall"]["band"], "Critical")
check("overall.counts", r["overall"]["counts"], {"Critical": 1, "High": 1, "Medium": 0, "Low": 2})

# Empty findings -> overall Low, no cohorts.
r = rs.score([], FS)
check("empty.band", r["overall"]["band"], "Low")
check("empty.cohorts", r["cohorts"], [])

# Monotonicity: none < recoverable < hard < hard+wide.
none = one(["web/app.js"], [])["score"]
recov = one(["engine/util.go"], [{"severityClass": "recoverable-refactor"}])["score"]
hard = one(["engine/api.go"], [{"severityClass": "hard-break"}])["score"]
wide = one(["engine/api.go", "web/app.js"], [{"severityClass": "hard-break"}])["score"]
if not (none < recov < hard < wide):
    failures.append(f"monotonicity: {none},{recov},{hard},{wide}")

# The 2-cohort fixture overall (matches custody risk-findings.jsonl).
r = rs.score([
    {"cohort": "engine API refactor", "cohortOrder": 1, "area": "backend", "files": ["engine/api.go"], "bcFindings": [{"severityClass": "hard-break", "category": "REMOVE_METHOD"}]},
    {"cohort": "web tweak", "cohortOrder": 2, "area": "frontend", "files": ["web/app.js"], "bcFindings": []},
], FS)
check("fixture.overall", r["overall"], {"band": "High", "score": 0.8, "counts": {"Critical": 0, "High": 1, "Medium": 0, "Low": 1}})

if failures:
    print("FAIL (%d):" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK — _risk_score.py reproduces custody score.js golden values")

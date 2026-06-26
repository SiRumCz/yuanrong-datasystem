#!/usr/bin/env python3
"""ABI tests for conclude-overview.py: deterministic band, fail-loud unknown, the
'only-unknown-blocks' policy, advisory-hint mismatch, meta stamping, overview.json.
Run: python3 test_conclude_overview.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "publish", "conclude-overview.py")
PR_JSON = json.dumps({"files": [
    {"path": "engine/api.go", "additions": 40, "deletions": 30},
    {"path": "web/app.js", "additions": 60, "deletions": 0},
]})

failures = []


def run(evidence, instance="pr-123", blocking="0", risk_band=None, pr_json=PR_JSON):
    d = tempfile.mkdtemp()
    ev_path = os.path.join(d, "evidence.json")
    if evidence is _MISSING:
        ev_path = os.path.join(d, "does-not-exist.json")
    else:
        if risk_band is not None and isinstance(evidence, dict):
            evidence = {**evidence, "risk_band": risk_band}
        with open(ev_path, "w") as fh:
            json.dump(evidence, fh)
    pr_path = os.path.join(d, "pr.json")
    with open(pr_path, "w") as fh:
        fh.write(pr_json)
    out_path = os.path.join(d, "overview.json")
    env = {**os.environ, "BLOCKING": blocking, "ENGINE_LOCAL": "1",
           "OVERVIEW_PR_JSON": pr_path, "OVERVIEW_OUT": out_path, "HEAD_SHA": "abc123"}
    r = subprocess.run([sys.executable, HOOK, ev_path, instance],
                       text=True, capture_output=True, env=env)
    verdict = json.loads(r.stdout.strip())
    overview = json.load(open(out_path)) if os.path.isfile(out_path) else None
    return verdict, overview


_MISSING = object()


def check(name, got, want):
    if got != want:
        failures.append(f"{name}: got {got!r}, want {want!r}")


TWO_COHORT = {"cohorts": [
    {"cohort": "engine API refactor", "cohortOrder": 1, "area": "backend",
     "files": ["engine/api.go"], "layers": [{"layer": "backend", "order": 1, "title": "x"}],
     "bcFindings": [{"severityClass": "hard-break", "category": "REMOVE_METHOD"}]},
    {"cohort": "web tweak", "cohortOrder": 2, "area": "frontend",
     "files": ["web/app.js"], "layers": [], "bcFindings": []},
], "summary": "does a thing"}

# A) Deterministic band from evidence + stats; passes (not blocked); meta stamped.
v, o = run(TWO_COHORT, risk_band="High")
check("A.blocked", v["blocked"], False)
check("A.conclusion", v["conclusion"], "clear")
check("A.overall", o["overall"], {"band": "High", "score": 0.8,
                                   "counts": {"Critical": 0, "High": 1, "Medium": 0, "Low": 1}})
check("A.meta.pr", o["meta"]["pr_number"], 123)
check("A.meta.sha", o["meta"]["head_sha"], "abc123")
check("A.layers_reattached", o["cohorts"][0]["layers"], [{"layer": "backend", "order": 1, "title": "x"}])
if "matches" in v["summary"] or "authoritative" in v["summary"]:
    failures.append("A.summary should not flag a mismatch when hint==computed (High)")

# B) Fail-loud: no cohorts list (engine's {"files":[]} agent-failed fallback) -> unknown + blocked.
v, o = run({"files": []})
check("B.blocked", v["blocked"], True)
check("B.conclusion", v["conclusion"], "blocked")
check("B.band", o["overall"]["band"], "unknown")

# C) Critical band still PASSES (only unknown blocks); advisory-hint mismatch is flagged.
crit = {"cohorts": [
    {"cohort": "c", "cohortOrder": 1, "files": ["engine/api.go", "web/app.js"],
     "bcFindings": [{"severityClass": "hard-break"}]}], "summary": "wide break"}
v, o = run(crit, risk_band="Medium")
check("C.band", o["overall"]["band"], "Critical")
check("C.blocked", v["blocked"], False)
if "authoritative" not in v["summary"]:
    failures.append(f"C.summary should flag hint mismatch; got {v['summary']!r}")

# D) Unreadable evidence -> unknown + blocked.
v, o = run(_MISSING)
check("D.blocked", v["blocked"], True)
check("D.band", o["overall"]["band"], "unknown")

# E) BLOCKING=1 forces blocked even on a clean run (symmetry with conclude-preflight).
v, o = run(TWO_COHORT, blocking="1")
check("E.blocked", v["blocked"], True)

# F) Missing file stats (empty pr.json) -> band unaffected, score uses 0/0 (tolerance).
v, o = run(TWO_COHORT, pr_json='{"files":[]}')
check("F.band", o["overall"]["band"], "High")

if failures:
    print("FAIL (%d):" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK — conclude-overview.py: deterministic band, fail-loud unknown, only-unknown-blocks, meta")

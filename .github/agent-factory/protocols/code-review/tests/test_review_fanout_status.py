#!/usr/bin/env python3
"""Regression test: the review fan-out's per-branch status line must reflect the
dimension's evidence VERDICT, not just its form checks.

Bug: `_render_leg_section` renders "✅ iteration n/m — all checks passed" from the
recorded `checks` map. For a review dimension, the form checks (evidence-present /
review-schema-valid / review-findings-anchored) pass even when the dimension returned
`REQUEST_CHANGES` with critical findings (e.g. the security dimension's deterministic
Cedar/Guardians engine findings) — so `review · security` read as "all checks passed"
(safe) while triage simultaneously gated `request-changes`. PRs #18/#19 showed exactly
this contradiction.

Fix (`render_pipeline_status_body` + `_review_verdict_note`, lib.py): for FAN-OUT
branch lines only, append "⚠️ request-changes (N critical[, M high])" when the branch's
evidence verdict is REQUEST_CHANGES or it carries critical/high findings. Non-fan-out
phases (preflight/overview/triage/fix/context/mrp) are untouched; clean (APPROVE)
dimensions stay plain.
"""
import json
import os
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "..", "..", "engine")
PROTO = os.path.join(HERE, "..", "protocol.json")
sys.path.insert(0, ENGINE)
import lib  # noqa: E402

# The review fanout's status-note config, mirroring protocol.json params.status_note.
# The engine note renderer is generic (lib._evidence_status_note); the REQUEST_CHANGES /
# critical|high vocabulary is protocol config, passed in here.
CFG = {"verdict_field": "verdict", "flag_verdicts": ["REQUEST_CHANGES"],
       "severity_field": "severity", "flag_severities": ["critical", "high"],
       "label": "request-changes"}

failures = []


def ok(name, cond):
    if not cond:
        failures.append(name)


def _wstate(d, pid, inst, phase, branch=None, checks=None):
    sf = lib.state_file(d, pid, inst, branch=branch, phase=phase)
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    yaml.safe_dump({"state": "done", "history": [{"iteration": 1,
                   "checks": checks or {"evidence-present": "pass",
                                        "review-schema-valid": "pass",
                                        "review-findings-anchored": "pass"}}]},
                   open(sf, "w"), sort_keys=False)


def _wev(d, pid, inst, phase, branch, obj):
    p = lib.output_artifact_path(d, pid, inst, branch=branch, phase=phase, kind="evidence")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(obj, open(p, "w"))


# ── Unit: _review_verdict_note ──
with tempfile.TemporaryDirectory() as d:
    pid, inst = "code-review", "pr-u"
    _wev(d, pid, inst, "review", "security",
         {"dimension": "security", "verdict": "REQUEST_CHANGES",
          "findings": [{"severity": "critical"}, {"severity": "critical"}]})
    ok("[note] REQUEST_CHANGES + 2 critical → flagged with count",
       lib._evidence_status_note(d, pid, inst, "review", "security", CFG) == " — ⚠️ request-changes (2 critical)")

    _wev(d, pid, inst, "review", "security",
         {"dimension": "security", "verdict": "COMMENT",
          "findings": [{"severity": "high"}, {"severity": "low"}]})
    ok("[note] findings (1 high) drive the flag even when verdict isn't REQUEST_CHANGES",
       lib._evidence_status_note(d, pid, inst, "review", "security", CFG) == " — ⚠️ request-changes (1 high)")

    _wev(d, pid, inst, "review", "security",
         {"dimension": "security", "verdict": "APPROVE",
          "findings": [{"severity": "low"}, {"severity": "medium"}]})
    ok("[note] APPROVE with only low/medium → clean (no flag)",
       lib._evidence_status_note(d, pid, inst, "review", "security", CFG) == "")

    p = lib.output_artifact_path(d, pid, inst, branch="security", phase="review", kind="evidence")
    os.remove(p)
    ok("[note] missing evidence (in-flight) → clean",
       lib._evidence_status_note(d, pid, inst, "review", "security", CFG) == "")


# ── Integration: render_pipeline_status_body, scoping ──
# `preflight` is a FANOUT whose direct legs are the adherence-chain clusters
# (adherence ∥ mm-compliance ∥ consistency ∥ security) + a separate
# `preflight-gate` AGENT phase. Fanout legs render as "**preflight · <leg>**";
# the gate renders as "**preflight-gate**" with an agent-style "✅ clear." note.
# (spec-solves-issue et al. are now nested one level down under adherence, so
# they are no longer direct preflight legs — we exercise a real direct leg.)
with tempfile.TemporaryDirectory() as d:
    pid, inst = "code-review", "pr-99"
    # preflight fanout: write one leg state to exercise the fanout rendering path
    _wstate(d, pid, inst, "preflight", branch="adherence",
            checks={"evidence-present": "pass"})
    # preflight-gate agent phase (must be done to get "✅ clear." note)
    _wstate(d, pid, inst, "preflight-gate",
            checks={"evidence-present": "pass", "preflight-gate-schema-valid": "pass"})
    _wstate(d, pid, inst, "review", branch="security")
    _wev(d, pid, inst, "review", "security",
         {"dimension": "security", "verdict": "REQUEST_CHANGES",
          "findings": [{"severity": "critical"}, {"severity": "critical"}]})
    _wstate(d, pid, inst, "review", branch="correctness")
    _wev(d, pid, inst, "review", "correctness",
         {"dimension": "correctness", "verdict": "APPROVE", "findings": []})
    body = lib.render_pipeline_status_body(d, pid, inst, PROTO)

    ok("[render] review·security shows the flagged verdict note",
       "**review · security** — ⚠️ request-changes (2 critical)" in body)
    ok("[render] review·correctness (APPROVE) stays plain — no flag",
       "**review · correctness**\n" in body and "**review · correctness** — ⚠️" not in body)
    ok("[render] preflight fanout leg renders without a verdict-note flag",
       "**preflight · adherence**\n" in body
       and "**preflight · adherence** — ⚠️" not in body)
    ok("[render] preflight-gate (agent phase) renders its '✅ clear.' note",
       "**preflight-gate**\n" in body
       and "✅ clear." in body.split("**preflight-gate**", 1)[1].split("**", 1)[0])


if failures:
    print("FAIL test_review_fanout_status:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - review fan-out status reflects the dimension verdict; non-fan-out phases unchanged")

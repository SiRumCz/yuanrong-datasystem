#!/usr/bin/env python3
"""Deterministic post-step: assemble the engine's mrp.json (custody Merge-Readiness
Pack shape) from the upstream phase EVIDENCE delivered via the engine's inputs[]
(preflight / overview / triage / context) plus the agent's judgment slices
(agent-out.json). No model calls.

This is the engine-native analog of custody's assemble-mrp.js. Custody's gather.js
pulls each phase's *conclude/scored* artifact; the engine instead delivers each
phase's *agent evidence*, so the per-cohort risk bands (which custody reads
pre-scored) are RE-DERIVED here with the engine's own deterministic scorer
(_risk_score — the score.js port that conclude-overview uses), never re-invented.
pack-map's buildAcceptancePlan is ported in pack_map.py.

The output JSON shape matches custody assemble-mrp.js: meta, overview, cohorts, risk,
routed_spots, spec_findings, plan_findings, smm_compliance, critique_ledger,
trajectory, rationale, acceptance_plan, riskBand, provenance. Every field defaults
defensively so the pack assembles even if the agent step or an upstream input is
absent.

ABI: assemble-mrp.py <task-context.json> <agent-out.json> [pr.json] > mrp.json
  task-context.json — the engine aw_context (carries .pr and .inputs.<phase> evidence)
  agent-out.json    — the agent's { rationale, routed_spots, critique_ledger, routed_questions }
  pr.json           — optional `gh pr view --json number,headRefOid,files` (file stats + head sha)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                          # pack_map
sys.path.insert(0, os.path.join(HERE, "..", "..", "publish"))    # _risk_score (engine scorer)
import pack_map  # noqa: E402
import _risk_score as rs  # noqa: E402

# Mirrors custody assemble-mrp.js: a DEMO smm_compliance placeholder injected while the
# mm-compliance gate is absent (it is always absent today).
DEMO_COMPLIANCE = {
    "demo": True,
    "verdict": "compliant",
    "divergences": [
        {"mm_doc": "adr/0007-example-decision.md",
         "decision": "(sample) Use a single shared cache",
         "contradiction": "(sample) introduces a second cache layer",
         "evidence_path": "(sample) app/x.js",
         "fix": "(sample) reuse the shared cache"},
    ],
}


def _read_json(path):
    if not path:
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _inputs(task_ctx):
    ic = (task_ctx or {}).get("inputs")
    return ic if isinstance(ic, dict) else {}


def _file_stats(pr):
    """Per-file {additions,deletions} keyed by path, mirroring conclude-overview's
    _file_stats. Absent => {} (the scorer then defaults files to 0/0 — the band is
    unaffected; only the score's size term degrades)."""
    stats = {}
    for f in ((pr or {}).get("files") or []):
        name = f.get("path") or f.get("filename")
        if name:
            stats[name] = {"additions": f.get("additions") or 0,
                           "deletions": f.get("deletions") or 0}
    return stats


def _attach_layers(scored_cohorts, input_cohorts):
    """score.js drops layers; reattach by (cohortOrder, cohort) identity (assemble.js)."""
    by_key = {}
    for c in (input_cohorts or []):
        if isinstance(c, dict):
            by_key[(c.get("cohortOrder"), c.get("cohort"))] = c
    out = []
    for sc in scored_cohorts:
        src = by_key.get((sc.get("cohortOrder"), sc.get("cohort"))) or {}
        out.append({**sc, "layers": src.get("layers") or []})
    return out


def _adherence_of(preflight, cid):
    """Spec/plan adherence status from the preflight EVIDENCE checks[] (the engine
    analog of custody's verdict.records[] check status)."""
    if not isinstance(preflight, dict):
        return None
    for c in (preflight.get("checks") or []):
        if isinstance(c, dict) and c.get("id") == cid:
            return c.get("status")
    return None


def assemble(task_ctx, agent, pr):
    inputs = _inputs(task_ctx)
    preflight = inputs.get("preflight") if isinstance(inputs.get("preflight"), dict) else None
    overview = inputs.get("overview") if isinstance(inputs.get("overview"), dict) else None
    context = inputs.get("context") if isinstance(inputs.get("context"), dict) else None
    agent = agent if isinstance(agent, dict) else {}

    # meta: pr_number from the engine task context (.pr) or pr.json; head_sha from pr.json.
    pr_number = None
    if isinstance(task_ctx, dict) and isinstance(task_ctx.get("pr"), int):
        pr_number = task_ctx.get("pr")
    if pr_number is None and isinstance(pr, dict) and isinstance(pr.get("number"), int):
        pr_number = pr.get("number")
    head_sha = (pr or {}).get("headRefOid") or ""

    # Re-derive per-cohort bands with the engine's own scorer (bands need only the
    # overview evidence; scores additionally use file_stats when available).
    cohorts_in = (overview or {}).get("cohorts") or []
    file_stats = _file_stats(pr)
    scored = rs.score(cohorts_in, file_stats)
    overall = scored["overall"]
    scored_cohorts = _attach_layers(scored["cohorts"], cohorts_in)

    phases = (context or {}).get("phases") or []
    total_tokens = (sum((p.get("token_count") or 0) for p in phases if isinstance(p, dict))
                    if phases else None)

    routed_spots = agent.get("routed_spots") if isinstance(agent.get("routed_spots"), list) else []
    critique_ledger = agent.get("critique_ledger") if isinstance(agent.get("critique_ledger"), list) else []
    routed_questions = agent.get("routed_questions") if isinstance(agent.get("routed_questions"), dict) else {}
    rationale = agent.get("rationale") or None

    return {
        "meta": {"pr_number": pr_number, "head_sha": head_sha},
        "overview": ({"summary": (overview or {}).get("summary") or "", "diagram": None}
                     if overview else None),
        "cohorts": scored_cohorts,
        "risk": ({"band": overall["band"], "score": overall["score"], "counts": overall["counts"]}
                 if overview else None),
        "routed_spots": routed_spots,
        "spec_findings": {"adherence": _adherence_of(preflight, "spec-adherence")},
        "plan_findings": {"adherence": _adherence_of(preflight, "plan-adherence")},
        "smm_compliance": DEMO_COMPLIANCE,
        "critique_ledger": critique_ledger,
        "trajectory": ({"phases": phases, "totalTokens": total_tokens} if context else None),
        "rationale": rationale,
        "acceptance_plan": pack_map.build_acceptance_plan(
            cohorts=scored_cohorts, routed_spots=routed_spots, questions=routed_questions),
        "riskBand": overall["band"] if overview else None,
        "provenance": {"run_ids": {}, "engines": ["codex"], "models": ["gpt-5.5"]},
    }


def main(argv):
    task_path = argv[0] if len(argv) > 0 else ""
    agent_path = argv[1] if len(argv) > 1 else ""
    pr_path = argv[2] if len(argv) > 2 else ""
    task_ctx = _read_json(task_path) or {}
    agent = _read_json(agent_path) or {}
    pr = _read_json(pr_path)
    sys.stdout.write(json.dumps(assemble(task_ctx, agent, pr)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

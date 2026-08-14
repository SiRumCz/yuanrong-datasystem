#!/usr/bin/env python3
"""Adapter: the assembled custody-shaped mrp.json -> the engine's evidence.json.

Deterministic. Surfaces the pack's judgment slices + the deterministic acceptance_plan
as the engine evidence, and derives a deterministic accept/hold recommendation from the
pack (risk band, L4-pending cohorts, spec/plan adherence) so conclude-mrp has a verdict
to roll up. Never fabricates: a missing/unreadable pack yields an explicit error
evidence with recommendation 'hold'.

ABI: to-evidence.py [mrp.json] [evidence.json]
"""
import json
import os
import sys

HOLD_BANDS = {"High", "Critical"}
STAGED_RUNGS = ["L2", "L4"]


def _read(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _routed_questions(pack):
    """The deterministic routed questions: per-cohort questions the acceptance_plan
    actually attached (L3 cohorts), keyed by cohort."""
    out = {}
    for c in ((pack.get("acceptance_plan") or {}).get("per_cohort") or []):
        if not isinstance(c, dict):
            continue
        q = c.get("routed_question")
        if q:
            out[str(c.get("cohort"))] = q
    return out


def _derive_acceptance(pack):
    plan = pack.get("acceptance_plan") or {}
    per = plan.get("per_cohort") or []
    band = pack.get("riskBand")
    spec = (pack.get("spec_findings") or {}).get("adherence")
    plan_adh = (pack.get("plan_findings") or {}).get("adherence")
    reasons = []
    hold = False
    if band in HOLD_BANDS:
        hold = True
        reasons.append(f"overall risk band is {band}")
    pending = [str(c.get("cohort")) for c in per if isinstance(c, dict) and c.get("l4_pending")]
    if pending:
        hold = True
        reasons.append("L4 (Cleanroom) pending for cohort(s): " + ", ".join(pending))
    for label, val in (("spec", spec), ("plan", plan_adh)):
        if val == "fail":
            hold = True
            reasons.append(f"{label}-adherence failed")
    if not hold:
        reasons.append("no blocking risk band, adherence failure, or L4-pending cohort")
    return {"recommendation": "hold" if hold else "accept", "reasons": reasons}


def _error_evidence(path):
    return {
        "rationale": None,
        "critique_ledger": [],
        "routed_questions": {},
        "routed_spots": [],
        "acceptance_plan": {"per_cohort": [], "staged_rungs": STAGED_RUNGS},
        "acceptance": {"recommendation": "hold", "reasons": ["mrp pack missing or unreadable"]},
        "riskBand": None,
        "smm_compliance": None,
        "meta": {},
        "pack": {"path": path, "error": True},
    }


def evidence_from_pack(path):
    pack = _read(path)
    if not isinstance(pack, dict):
        return _error_evidence(path)
    return {
        "rationale": pack.get("rationale"),
        "critique_ledger": pack.get("critique_ledger") or [],
        "routed_questions": _routed_questions(pack),
        "routed_spots": pack.get("routed_spots") or [],
        "acceptance_plan": pack.get("acceptance_plan") or {"per_cohort": [], "staged_rungs": STAGED_RUNGS},
        "acceptance": _derive_acceptance(pack),
        "riskBand": pack.get("riskBand"),
        "smm_compliance": pack.get("smm_compliance"),
        "meta": pack.get("meta") or {},
        "pack": {"path": path, "error": False},
    }


def main(argv):
    pack_path = argv[0] if len(argv) > 0 else "/tmp/gh-aw/mrp.json"
    out_path = argv[1] if len(argv) > 1 else ""
    payload = json.dumps(evidence_from_pack(pack_path), separators=(",", ":"))
    if out_path:
        d = os.path.dirname(os.path.abspath(out_path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

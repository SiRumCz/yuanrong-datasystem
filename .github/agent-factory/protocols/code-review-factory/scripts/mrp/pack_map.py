#!/usr/bin/env python3
"""Pure acceptance-plan mapping for the MRP — a verbatim logic port of custody's
app/backend/component/mrp/workflow/scripts/pack-map.js. No I/O.

Per-cohort rung from the risk band. Capture-only rungs L0/L1/L3 are implemented;
L2 (state-intent, AI-diffed) and L4 (Cleanroom derive) are staged — Medium maps to
L1 and Critical to L3 until they land (Critical also flags l4_pending so the
reduction is visible). Imported by assemble-mrp.py.
"""

RUNG_FOR_BAND = {"Low": "L0", "Medium": "L1", "High": "L3", "Critical": "L3"}
STAGED_RUNGS = ["L2", "L4"]
QUESTION_RUNGS = {"L3"}  # rungs that carry a routed_question


def build_acceptance_plan(cohorts=None, routed_spots=None, questions=None):
    spots = routed_spots if isinstance(routed_spots, list) else []
    q = questions if isinstance(questions, dict) else {}
    per_cohort = []
    for c in (cohorts if isinstance(cohorts, list) else []):
        if not isinstance(c, dict):
            continue
        band = c.get("band") or "Low"
        rung = RUNG_FOR_BAND.get(band, "L0")
        cohort = c.get("cohort")
        per_cohort.append({
            "cohort": cohort,
            "band": band,
            "rung": rung,
            "routed_question": (q.get(cohort, "") if rung in QUESTION_RUNGS else ""),
            "spot_ids": [s.get("spot_id") for s in spots
                         if isinstance(s, dict) and s.get("cohort") == cohort],
            "l4_pending": band == "Critical",
        })
    return {"per_cohort": per_cohort, "staged_rungs": STAGED_RUNGS}

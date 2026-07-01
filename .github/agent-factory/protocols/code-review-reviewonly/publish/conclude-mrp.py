#!/usr/bin/env python3
"""Conclude hook for the mrp (merge-readiness pack) phase. Rolls the deterministic
acceptance recommendation (derived from the pack by to-evidence.py) into
clear/neutral/blocked and writes a custody-shaped verdict.json (records[] + verdict +
meta).

Advisory by design: a `hold` annotates the terminal verdict but does NOT halt — the
mrp state declares no on_blocked, so even a blocked conclude only annotates. `blocked`
is gated on the engine BLOCKING signal for symmetry with conclude-preflight; with mrp
unconfigured to block, a hold surfaces as conclusion `neutral` and the chain still
reaches done.

ABI: conclude-mrp.py <evidence.json> <instance-key>;  env BLOCKING ("1"/"0").
Prints {"conclusion","summary","blocked"}.
"""
import json
import os
import sys


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    inst = sys.argv[2] if len(sys.argv) > 2 else ""
    blocking = os.environ.get("BLOCKING", "") == "1"
    try:
        with open(ev_path) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    if not isinstance(evidence, dict):
        evidence = {}

    acceptance = evidence.get("acceptance") if isinstance(evidence.get("acceptance"), dict) else {}
    recommendation = acceptance.get("recommendation")
    reasons = [r for r in (acceptance.get("reasons") or []) if isinstance(r, str)]
    risk_band = evidence.get("riskBand")
    plan = evidence.get("acceptance_plan") if isinstance(evidence.get("acceptance_plan"), dict) else {}
    per_cohort = plan.get("per_cohort") or []

    is_hold = recommendation == "hold"
    blocked = bool(is_hold and blocking)
    conclusion = "blocked" if blocked else ("neutral" if is_hold else "clear")

    # custody-shaped verdict.json payload (records[] + verdict + meta echo).
    records = []
    for c in per_cohort:
        if isinstance(c, dict):
            records.append({"type": "cohort", "cohort": c.get("cohort"), "band": c.get("band"),
                            "rung": c.get("rung"), "l4_pending": c.get("l4_pending"),
                            "routed_question": c.get("routed_question") or ""})
    records.append({"type": "verdict", "recommendation": recommendation or "accept",
                    "riskBand": risk_band, "reasons": reasons, "blocking": bool(blocking)})
    payload = {"records": records}

    meta = evidence.get("meta") if isinstance(evidence.get("meta"), dict) else {}
    if meta.get("pr_number") is not None or meta.get("head_sha"):
        payload["meta"] = {"pr_number": meta.get("pr_number"), "head_sha": meta.get("head_sha") or ""}
    elif inst.startswith("pr-") and inst[3:].isdigit():
        payload["meta"] = {"pr_number": int(inst[3:]), "head_sha": os.environ.get("HEAD_SHA", "")}

    out_path = os.environ.get("VERDICT_OUT", "/tmp/gh-aw/verdict.json")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump(payload, fh)
    except OSError:
        pass

    n = len(per_cohort)
    if is_hold:
        summary = f"MRP: hold ({n} cohort(s))" + (" — " + "; ".join(reasons[:3]) if reasons else "")
    else:
        summary = f"MRP: accept ({n} cohort(s); risk band {risk_band})."
    print(json.dumps({"conclusion": conclusion, "summary": summary, "blocked": blocked}))


if __name__ == "__main__":
    main()

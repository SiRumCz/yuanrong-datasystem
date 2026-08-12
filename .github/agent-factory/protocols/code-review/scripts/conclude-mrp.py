#!/usr/bin/env python3
"""`code` step for the mrp (merge-readiness pack) phase. Rolls the deterministic
acceptance recommendation (derived from the pack by to-evidence.py) into a
conclusion and writes a custody-shaped verdict.json (records[] + verdict + meta).

This is PUBLICATION, not evaluation: it emits the pack's verdict artifact. It
never vetoes, which is exactly why it is a `code` step rather than a `conclude`
hook — `conclude`'s one job is to read evidence and decide an exit code, and this
always exits 0. Advisory by design: a `hold` annotates the terminal verdict but
does not halt.

ABI (4.0.0, BPMN Script Task): conclude-mrp.py <workdir> <instance-key>, with the
mrp agent's evidence materialized at <workdir>/inputs/evidence.json from the
node's declared `inputs`. Prints {"conclusion","summary"}.
"""
import json
import os
import sys


def main():
    workdir = sys.argv[1] if len(sys.argv) > 1 else ""
    inst = sys.argv[2] if len(sys.argv) > 2 else ""
    ev_path = os.path.join(workdir, "inputs", "evidence.json") if workdir else ""
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

    # A `code` hook's conclusion vocabulary is exactly {success, failure} (or
    # absent): lib.finalize_code_result fails ANY other value CLOSED, so the old
    # "clear"/"neutral" strings would have halted the run. mrp is advisory by
    # design and never vetoes -- a `hold` annotates the verdict, it does not stop
    # the pipeline -- so this always reports success and carries the
    # recommendation in the summary and in verdict.json.
    is_hold = recommendation == "hold"
    conclusion = "success"

    # custody-shaped verdict.json payload (records[] + verdict + meta echo).
    records = []
    for c in per_cohort:
        if isinstance(c, dict):
            records.append({"type": "cohort", "cohort": c.get("cohort"), "band": c.get("band"),
                            "rung": c.get("rung"), "l4_pending": c.get("l4_pending"),
                            "routed_question": c.get("routed_question") or ""})
    records.append({"type": "verdict", "recommendation": recommendation or "accept",
                    "riskBand": risk_band, "reasons": reasons})
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
    print(json.dumps({"conclusion": conclusion, "summary": summary}))


if __name__ == "__main__":
    main()

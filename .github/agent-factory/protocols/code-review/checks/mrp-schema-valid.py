#!/usr/bin/env python3
"""Check: the mrp evidence carries the merge-readiness-pack-derived shape.

Validates the substance of the engine mrp evidence (deterministically assembled from
the pack): the four judgment slices + the deterministic acceptance_plan + acceptance
recommendation. Advisory — prints one {"check","pass","feedback"} object and always
exits 0.

ABI: mrp-schema-valid.py <evidence.json> <diff.txt> <changed-files.txt>
"""
import json
import sys

BANDS = {"Low", "Medium", "High", "Critical"}
RECOMMENDATIONS = {"accept", "hold"}


def is_str(v):
    return isinstance(v, str)


def is_nonneg_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def emit(ok, feedback):
    print(json.dumps({"check": "mrp-schema-valid", "pass": ok, "feedback": feedback}))


def _validate_rationale(r, problems):
    if r is None:
        return  # clean absence (agent produced none) — not an error here
    if not isinstance(r, dict):
        problems.append("rationale is not an object")
        return
    if not is_str(r.get("summary")):
        problems.append("rationale.summary is not a string")
    im = r.get("intentMatch")
    if im is not None and im not in ("aligned", "partial", "unclear"):
        problems.append("rationale.intentMatch not in aligned/partial/unclear")
    kps = r.get("keyPoints")
    if not isinstance(kps, list):
        problems.append("rationale.keyPoints is not a list")
    else:
        for i, kp in enumerate(kps):
            # Each key point must carry a verbatim snippet + its source (conversation|walkthrough),
            # matching the custody clear-rationale shape.
            if (not isinstance(kp, dict) or not is_str(kp.get("point"))
                    or not is_str(kp.get("snippet"))
                    or kp.get("source") not in ("conversation", "walkthrough")):
                problems.append(f"rationale.keyPoints[{i}] missing string point/snippet or source not in conversation/walkthrough")
                break


def _validate_critique(ledger, problems):
    if not isinstance(ledger, list):
        problems.append("critique_ledger is not a list")
        return
    for i, e in enumerate(ledger):
        if not isinstance(e, dict):
            problems.append(f"critique_ledger[{i}] is not an object")
            break
        for k in ("dimension", "severity", "title"):
            if not is_str(e.get(k)):
                problems.append(f"critique_ledger[{i}].{k} is not a string")
                break
        if "path" in e and e.get("path") is not None and not is_str(e.get("path")):
            problems.append(f"critique_ledger[{i}].path is not a string/null")
        if "line" in e and e.get("line") is not None and not isinstance(e.get("line"), int):
            problems.append(f"critique_ledger[{i}].line is not an integer/null")


def _validate_routed_questions(rq, problems):
    if not isinstance(rq, dict):
        problems.append("routed_questions is not an object")
        return
    for k, v in rq.items():
        if not is_str(v):
            problems.append(f"routed_questions[{k!r}] value is not a string")
            break


def _validate_acceptance(acc, problems):
    if not isinstance(acc, dict):
        problems.append("acceptance is not an object")
        return
    if acc.get("recommendation") not in RECOMMENDATIONS:
        problems.append(f"acceptance.recommendation {acc.get('recommendation')!r} not in {sorted(RECOMMENDATIONS)}")
    reasons = acc.get("reasons")
    if not isinstance(reasons, list) or not all(is_str(x) for x in reasons):
        problems.append("acceptance.reasons is not a list of strings")


def _validate_plan(plan, problems):
    if not isinstance(plan, dict):
        problems.append("acceptance_plan is not an object")
        return
    per = plan.get("per_cohort")
    if not isinstance(per, list):
        problems.append("acceptance_plan.per_cohort is not a list")
    else:
        for i, c in enumerate(per):
            if not isinstance(c, dict):
                problems.append(f"acceptance_plan.per_cohort[{i}] is not an object")
                break
            if c.get("band") not in BANDS:
                problems.append(f"acceptance_plan.per_cohort[{i}].band {c.get('band')!r} not in {sorted(BANDS)}")
                break
            if not is_str(c.get("rung")):
                problems.append(f"acceptance_plan.per_cohort[{i}].rung is not a string")
                break
    if not isinstance(plan.get("staged_rungs"), list):
        problems.append("acceptance_plan.staged_rungs is not a list")


def _validate_meta(meta, problems):
    if not isinstance(meta, dict):
        problems.append("meta is not an object")
        return
    if "pr_number" in meta and meta.get("pr_number") is not None and not is_nonneg_int(meta.get("pr_number")):
        problems.append("meta.pr_number is not a non-negative integer")
    if "head_sha" in meta and not is_str(meta.get("head_sha")):
        problems.append("meta.head_sha is not a string")


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "", encoding="utf-8") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        emit(False, f"evidence unreadable / not JSON: {exc}")
        return

    if not isinstance(ev, dict):
        emit(False, "evidence is not a JSON object")
        return

    problems = []
    for key in ("rationale", "critique_ledger", "routed_questions", "acceptance", "acceptance_plan"):
        if key not in ev:
            problems.append(f"missing required key `{key}`")
    _validate_rationale(ev.get("rationale"), problems)
    _validate_critique(ev.get("critique_ledger"), problems)
    _validate_routed_questions(ev.get("routed_questions"), problems)
    _validate_acceptance(ev.get("acceptance"), problems)
    _validate_plan(ev.get("acceptance_plan"), problems)
    _validate_meta(ev.get("meta"), problems)

    if problems:
        emit(False, "mrp schema: " + "; ".join(problems[:8]))
    else:
        emit(True, "")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Pure port of custody reviewers/shape.js deriveGate. No I/O.

NO reviewers present => incomplete: an empty/vacuous triage is not a pass; it
means the review did not happen. Otherwise critical/high => request-changes,
medium => warn, else pass.
"""


def derive_gate(summary):
    sev = (summary or {}).get("by_severity") or {}
    counts = {k: int(sev.get(k) or 0) for k in ("critical", "high", "medium", "low")}
    present = (summary or {}).get("present") or []
    if not present:
        return {"verdict": "incomplete", "counts": counts}
    if counts["critical"] or counts["high"]:
        verdict = "request-changes"
    elif counts["medium"]:
        verdict = "warn"
    else:
        verdict = "pass"
    return {"verdict": verdict, "counts": counts}

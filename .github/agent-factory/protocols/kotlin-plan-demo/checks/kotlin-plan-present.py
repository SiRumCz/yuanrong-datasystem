#!/usr/bin/env python3
"""Check: kotlin-plan-present — the agent's plan is a non-empty, analyzable-looking
Kotlin script in evidence.plan_kts.

This verifies the *form* of the plan (per the engine's "check the form, not the
substance" contract) — NOT whether the plan is safe; proving safety is the taint
analyzer's job (Meijer, "In Code They Think; In Proof We Trust"). It fails
(-> iterate with feedback) when plan_kts is missing/empty, doesn't look like Kotlin
(no `fun`/`val`), or gives a sink a computed (unverifiable) destination.

ABI: kotlin-plan-present.py <evidence.json> <diff.txt> <changed-files.txt>
Emits one {check,pass,feedback} JSON object and always exits 0.
"""
import json
import re
import sys

CHECK = "kotlin-plan-present"


def emit(passed, feedback):
    print(json.dumps({"check": CHECK, "pass": passed, "feedback": feedback},
                     ensure_ascii=False))
    sys.exit(0)


def main():
    if len(sys.argv) < 2:
        emit(False, "usage: kotlin-plan-present.py <evidence.json> <diff> <changed-files>")
    try:
        with open(sys.argv[1]) as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        emit(False, f"evidence unreadable/not JSON: {exc}")
    if not isinstance(ev, dict):
        emit(False, "evidence is not a JSON object")

    plan = ev.get("plan_kts")
    if not isinstance(plan, str) or not plan.strip():
        emit(False, "plan_kts missing or empty — emit the plan as a Kotlin .kts string")

    reasons = []
    if "fun " not in plan:
        reasons.append("no `fun ` — the plan must be a Kotlin function")
    if not re.search(r"\bval\s+\w+\s*=", plan):
        reasons.append("no `val` binding — data must flow through named val bindings")

    # Sinks must carry a LITERAL destination so the analyzer can check it against the
    # policy. Flag a url=/path= argument whose value is a function call (dynamic dest).
    for m in re.finditer(r"\b(url|path)\s*=\s*([A-Za-z_]\w*)\s*\(", plan):
        reasons.append(
            f"sink `{m.group(1)}=` uses a computed destination `{m.group(2)}(...)` — "
            f"use a string literal so the destination can be verified")

    if reasons:
        emit(False, "; ".join(reasons))
    emit(True, "plan_kts present and analyzable-looking")


if __name__ == "__main__":
    main()

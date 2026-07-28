#!/usr/bin/env python3
"""verify-plan-cert.py — post-step: verify the plan certificate and record the verdict
into evidence under agent_security.plan_cert_verify (parity with the guardians wrapper).

Reads agent_security.{plan_ast, plan_cert} from /tmp/gh-aw/evidence.json, runs the
stdlib proof-carrying checker (_plan_cert.verify: grounding + closure + leak), and merges
the verdict back. Advisory; fail-open. Usage: verify-plan-cert.py <evidence.json>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plan_cert  # noqa: E402


def main():
    if len(sys.argv) < 2:
        return 0
    ev_path = sys.argv[1]
    try:
        with open(ev_path) as fh:
            ev = json.load(fh)
        if not isinstance(ev, dict):
            return 0
    except (OSError, ValueError):
        return 0

    sec = ev.get("agent_security")
    sec = sec if isinstance(sec, dict) else {}
    try:
        verdict = _plan_cert.verify(sec.get("plan_ast"), sec.get("cert"))
    except Exception as exc:  # never break the step
        verdict = {"verdict": "n/a", "status": "n/a", "reason": f"cert verify error: {exc}"}

    try:
        sec = ev.setdefault("agent_security", {})
        if isinstance(sec, dict):
            sec["cert_verify"] = verdict
            with open(ev_path, "w") as fh:
                json.dump(ev, fh, ensure_ascii=False)
    except (OSError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Advisory Layer-B honesty check — computes divergence IN-CHECK (trusted zone 3).

Reads the agent's self-declared `agent_security.plan_ast` from evidence and the
engine-global normalized trajectory from `TRAJECTORY_PATH` (the JSON list that
`agentic-engine.yml` writes via `engine/actions.py`), then asks
`engine/divergence.verify_divergence` whether the declared plan accounts for the
effectful actions the trajectory shows.

This replaces the per-agent `verify-plan-honesty.py` post-step: the verdict is
now derived by the trusted checks job over the independently-normalized
trajectory, not by the agent's own job over agent-produced evidence. Mirrors the
thin-wrapper pattern of `honesty-attest-demo/checks/claims-attested.py`.

ALWAYS pass (advisory — Layer B never gates); the verdict is disclosed as
feedback. Fail-open on any error. ABI: <check> <evidence> <diff> <changed-files>;
reads `TRAJECTORY_PATH` from env."""
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "engine"))
import divergence  # noqa: E402

CHECK = "plan-honesty-valid"


def _load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def main():
    ev = _load_json(sys.argv[1] if len(sys.argv) > 1 else "")
    sec = ev.get("agent_security") if isinstance(ev, dict) else None
    plan_ast = sec.get("plan_ast") if isinstance(sec, dict) else None
    trajectory = _load_json(os.environ.get("TRAJECTORY_PATH", ""))
    try:
        h = divergence.verify_divergence(plan_ast, trajectory)
    except Exception as exc:  # advisory — never break the check (fail-open)
        h = {"verdict": "uninspected", "undeclared": [], "inspected_channels": [],
             "not_inspected": ["shell-command internals", f"(honesty check error: {exc})"]}
    verdict = h.get("verdict", "n/a")
    fb = "" if verdict in ("pass", "n/a") else \
        f"honesty (advisory, {verdict}): undeclared={h.get('undeclared')}; " \
        f"inspected={h.get('inspected_channels')}; not_inspected={h.get('not_inspected')}"
    print(json.dumps({"check": CHECK, "pass": True, "feedback": fb}))


if __name__ == "__main__":
    main()

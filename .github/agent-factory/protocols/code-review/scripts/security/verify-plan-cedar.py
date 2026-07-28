#!/usr/bin/env python3
"""verify-plan-cedar.py — post-step: run Cedar (PARC authorization) over the agent's
plan_ast and record the verdict under agent_security.cedar (parity with guardians/cert).

Reads agent_security.plan_ast, hands it to run-cedar-plan.js (Node + @cedar-policy/
cedar-wasm), and merges the verdict back. Advisory; fail-open (node/cedar-wasm absent or
no plan_ast -> verdict "n/a", exit 0). Usage: verify-plan-cedar.py <evidence.json> <cedar-default-dir>
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, "run-cedar-plan.js")


def _merge(ev_path, verdict):
    try:
        with open(ev_path) as fh:
            ev = json.load(fh)
        if not isinstance(ev, dict):
            return
        sec = ev.setdefault("agent_security", {})
        if isinstance(sec, dict):
            sec["cedar"] = verdict
        with open(ev_path, "w") as fh:
            json.dump(ev, fh, ensure_ascii=False)
    except (OSError, ValueError):
        pass


def main():
    if len(sys.argv) < 3:
        return 0
    ev_path, policy_dir = sys.argv[1], sys.argv[2]
    try:
        with open(ev_path) as fh:
            ev = json.load(fh)
        sec = ev.get("agent_security") if isinstance(ev, dict) else None
        plan_ast = sec.get("plan_ast") if isinstance(sec, dict) else None
    except (OSError, ValueError):
        return 0
    if not isinstance(plan_ast, dict) or not isinstance(plan_ast.get("steps"), list):
        _merge(ev_path, {"verdict": "n/a", "status": "n/a", "reason": "no plan_ast to authorize"})
        return 0

    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(plan_ast, tf)
            pf = tf.name
        out = subprocess.run(["node", DRIVER, pf, policy_dir],
                             capture_output=True, text=True, timeout=120, cwd=HERE)
        if out.returncode != 0 or not out.stdout.strip():
            err = (out.stderr or "").strip().splitlines()[-1:] or ["no output"]
            _merge(ev_path, {"verdict": "n/a", "status": "n/a",
                             "reason": f"cedar did not run (rc={out.returncode}): {err[0][:200]}"})
            return 0
        r = json.loads(out.stdout)
        if not isinstance(r, dict):
            _merge(ev_path, {"verdict": "n/a", "status": "n/a", "reason": "cedar output not a JSON object"})
            return 0
    except Exception as exc:
        _merge(ev_path, {"verdict": "n/a", "status": "n/a", "reason": f"cedar unavailable: {exc}"})
        return 0
    finally:
        try:
            os.unlink(pf)
        except (OSError, NameError):
            pass

    _merge(ev_path, {"verdict": r.get("verdict", "n/a"), "status": r.get("status", "ok"),
                     "flags": r.get("flags") or []})
    return 0


if __name__ == "__main__":
    sys.exit(main())

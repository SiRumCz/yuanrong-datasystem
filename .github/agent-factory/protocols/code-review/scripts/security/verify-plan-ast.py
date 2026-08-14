#!/usr/bin/env python3
"""verify-plan-ast.py — run the Guardians verifier over an agent's `plan_ast` and
record the verdict back into its evidence (advisory).

Every code-review agent emits a `plan_ast` — a restricted workflow AST
`{ "steps": [ { "tool", "args": {param: literal | {"$ref": sym}}, "result"? } ] }` —
which is exactly the input `verify_driver.py` (the metareflection/guardians driver)
consumes. This wrapper, run as a POST-agent step (where python3.11 + guardians + z3
are installed), extracts that plan_ast, verifies it, and merges the result into
`/tmp/gh-aw/evidence.json` under `plan_ast_guardians`. It NEVER gates — the verdict is
advisory; a downstream stdlib check surfaces it.

Fail-open by construction: a missing plan_ast, absent toolchain, or verifier error
records `{"status": "n/a", ...}` and still exits 0, so the live pipeline is unaffected.

Usage: verify-plan-ast.py <evidence.json> <guardians-policy.yaml> [custom-policy.yaml]
Stdlib only (json / subprocess / tempfile); the heavy lifting (guardians, z3) lives in
verify_driver.py, invoked as a subprocess on python3.11.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _toolmap  # noqa: E402  (shared tool-name classifier — single source of truth)

DRIVER = os.path.join(HERE, "verify_driver.py")


def _normalize(plan_ast):
    """Map each plan_ast step's tool to the canonical guardians vocabulary via _toolmap,
    so the allowlist and taint rules recognize it regardless of the agent's naming
    (Claude Read/Bash, codex shell/edit, or free-form readFile/curlPost). Unrecognized
    or pure-compute tools become `compute` (benign) so the flow edges survive."""
    steps = []
    for s in plan_ast.get("steps", []):
        if not isinstance(s, dict):
            continue
        canon = _toolmap.canonical(s.get("tool"), s.get("args")) or "compute"
        steps.append({**s, "tool": canon, "args": _toolmap.canonical_args(canon, s.get("args"))})
    return {"steps": steps}


def _merge(evidence_path, verdict):
    """Merge the guardians verdict into evidence.json under agent_security.plan_ast_guardians."""
    try:
        with open(evidence_path) as fh:
            ev = json.load(fh)
        if not isinstance(ev, dict):
            return  # nothing we can safely attach to
        sec = ev.setdefault("agent_security", {})
        if isinstance(sec, dict):
            sec["guardians"] = verdict
        with open(evidence_path, "w") as fh:
            json.dump(ev, fh, ensure_ascii=False)
    except (OSError, ValueError):
        pass  # fail-open: never break the step over a record-keeping write


def main():
    if len(sys.argv) < 3:
        # nothing to do; not an error we want to surface
        return 0
    ev_path, policy = sys.argv[1], sys.argv[2]
    custom = sys.argv[3] if len(sys.argv) > 3 else None

    # 1. pull plan_ast out of the evidence
    try:
        with open(ev_path) as fh:
            ev = json.load(fh)
        sec = ev.get("agent_security") if isinstance(ev, dict) else None
        plan_ast = sec.get("plan_ast") if isinstance(sec, dict) else None
    except (OSError, ValueError):
        return 0  # no readable evidence -> leave it alone

    if not isinstance(plan_ast, dict) or not isinstance(plan_ast.get("steps"), list):
        _merge(ev_path, {"verdict": "n/a", "status": "n/a", "reason": "no plan_ast workflow to verify"})
        return 0

    # 2. normalize tool names to the canonical vocabulary, then hand to the real
    #    guardians driver (python3.11 + guardians + z3)
    plan_ast = _normalize(plan_ast)
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(plan_ast, tf)
            ast_file = tf.name
        cmd = [sys.executable, DRIVER, ast_file, policy] + ([custom] if custom else [])
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        # verify_driver crashes (rc!=0, empty stdout) when guardians/z3 are absent —
        # that must be recorded as n/a, NOT mistaken for a clean "ok" verdict.
        if out.returncode != 0 or not out.stdout.strip():
            err = (out.stderr or "").strip().splitlines()[-1:] or ["no output"]
            _merge(ev_path, {"verdict": "n/a", "status": "n/a",
                             "reason": f"guardians did not run (rc={out.returncode}): {err[0][:200]}"})
            return 0
        result = json.loads(out.stdout)
        if not isinstance(result, dict):
            _merge(ev_path, {"verdict": "n/a", "status": "n/a", "reason": "guardians output not a JSON object"})
            return 0
    except Exception as exc:  # toolchain missing / driver error / bad output
        _merge(ev_path, {"verdict": "n/a", "status": "n/a", "reason": f"guardians unavailable: {exc}"})
        return 0
    finally:
        try:
            os.unlink(ast_file)
        except (OSError, NameError):
            pass

    # 3. record the verdict (advisory). Only LOCKED violations (exfiltration/destructive)
    #    count -- non-LOCKED allowlist/scope findings are noise, not a real breach.
    violations = result.get("violations") or []
    locked = [v for v in violations if isinstance(v, dict) and v.get("locked")]
    ok = not locked
    _merge(ev_path, {
        "verdict": "pass" if ok else "fail",
        "status": "ok",
        "ok": ok,
        "violations": locked,
        "warnings": result.get("warnings") or [],
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())

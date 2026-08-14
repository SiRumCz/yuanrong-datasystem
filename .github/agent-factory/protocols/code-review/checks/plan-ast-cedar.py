#!/usr/bin/env python3
"""Check: surface the Cedar authorization verdict on plan_ast (ADVISORY).

Cedar runs in the agent's post-step (verify-plan-cedar.py, Node + cedar-wasm), which
records its verdict into evidence under agent_security.cedar. This stdlib check reads
that recorded verdict and reports it. Wired on_fail:advisory — records, never blocks.

Verdict mapping (from agent_security.cedar):
  missing / verdict "n/a" -> pass (cedar did not run)
  verdict "pass"          -> pass (all planned actions authorized)
  verdict "fail"          -> pass:false (a planned action was denied), feedback lists the rule ids

ABI: plan-ast-cedar.py <evidence.json> <diff> <changed>. Emits {check,pass,feedback}, exits 0.
"""
import json
import sys

CHECK = "plan-ast-cedar"


def emit(passed, feedback):
    print(json.dumps({"check": CHECK, "pass": passed, "feedback": feedback}, ensure_ascii=False))
    sys.exit(0)


def main():
    if len(sys.argv) < 2:
        emit(True, "no evidence path (advisory)")
    try:
        with open(sys.argv[1]) as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        emit(True, "evidence unreadable (advisory)")
    if not isinstance(ev, dict):
        emit(True, "evidence not an object (advisory)")

    sec = ev.get("agent_security")
    c = sec.get("cedar") if isinstance(sec, dict) else None
    verdict = c.get("verdict") if isinstance(c, dict) else None
    if not isinstance(c, dict) or verdict == "n/a":
        reason = c.get("reason") if isinstance(c, dict) else "not recorded"
        emit(True, f"cedar: n/a ({reason})")
    if verdict == "pass":
        emit(True, "cedar: pass — all planned actions authorized")

    flags = c.get("flags") or []
    ids = [str(f.get("determining_id") or f.get("action")) for f in flags if isinstance(f, dict)]
    locked = [f for f in flags if isinstance(f, dict) and f.get("locked")]
    emit(False, f"cedar: fail — {len(flags)} denied action(s)"
                f"{f', {len(locked)} LOCKED' if locked else ''}: {', '.join(ids[:5])}")


if __name__ == "__main__":
    main()

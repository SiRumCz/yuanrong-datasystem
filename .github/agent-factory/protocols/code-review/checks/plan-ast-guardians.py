#!/usr/bin/env python3
"""Check: surface the Guardians verdict on an agent's plan_ast (ADVISORY).

The real Guardians verifier runs in the agent's post-steps (verify-plan-ast.py, where
python3.11 + guardians + z3 are available) and records its result into evidence under
`plan_ast_guardians`. This stdlib check reads that recorded verdict and reports it, so
it shows up as a verdict in the engine status. It is wired `on_fail: advisory` — a
violation is recorded, never blocks or iterates.

Verdict mapping:
  no plan_ast_guardians / status "n/a" -> pass (guardians did not run; fail-open)
  ok / no violations                   -> pass
  violations present                   -> pass:false, feedback lists them (advisory only)

ABI: plan-ast-guardians.py <evidence.json> <diff.txt> <changed-files.txt>
Prints one {"check","pass","feedback"} object and always exits 0.
"""
import json
import sys

CHECK = "plan-ast-guardians"


def emit(passed, feedback):
    print(json.dumps({"check": CHECK, "pass": passed, "feedback": feedback},
                     ensure_ascii=False))
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
    g = sec.get("guardians") if isinstance(sec, dict) else None
    verdict = g.get("verdict") if isinstance(g, dict) else None
    if not isinstance(g, dict) or verdict == "n/a":
        reason = g.get("reason") if isinstance(g, dict) else "not recorded"
        emit(True, f"guardians: n/a ({reason})")
    if verdict == "pass":
        emit(True, f"guardians: pass ({len(g.get('warnings') or [])} warning(s))")

    violations = g.get("violations") or []
    names = [str(v.get("name") or v) for v in violations if isinstance(v, (dict, str))]
    locked = [v for v in violations if isinstance(v, dict) and v.get("locked")]
    emit(False, f"guardians: fail — {len(violations)} violation(s)"
                f"{f', {len(locked)} LOCKED' if locked else ''}: {', '.join(names[:5])}")


if __name__ == "__main__":
    main()

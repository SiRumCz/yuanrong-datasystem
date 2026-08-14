#!/usr/bin/env python3
"""Check: surface the proof-carrying certificate verdict (ADVISORY).

The proof-carrying verification runs in the agent's post-step (verify-plan-cert.py,
stdlib), which records its result into evidence under agent_security.plan_cert_verify.
This check reads that recorded verdict and reports it, so it shows up in the engine
status. Wired on_fail:advisory — records, never blocks.

Verdict mapping (from agent_security.plan_cert_verify):
  missing / status "n/a"                 -> pass (no certificate to verify)
  certificate_valid && !leak             -> pass
  !certificate_valid (grounding/closure) -> pass:false (proof rejected)
  leak                                    -> pass:false (valid proof, source->sink leak)

ABI: plan-cert-verify.py <evidence.json> <diff> <changed>. Emits {check,pass,feedback},
always exits 0. Stdlib only.
"""
import json
import sys

CHECK = "plan-cert-verify"


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
    v = sec.get("cert_verify") if isinstance(sec, dict) else None
    verdict = v.get("verdict") if isinstance(v, dict) else None
    if not isinstance(v, dict) or verdict == "n/a":
        reason = v.get("reason") if isinstance(v, dict) else "not recorded"
        emit(True, f"cert: n/a ({reason})")
    if verdict == "pass":
        emit(True, f"cert: pass — verified {v.get('paths_verified', 0)} path(s), no leak")

    # verdict == fail: certificate rejected, or valid-but-leaks
    if not v.get("certificate_valid", False):
        why = "; ".join(v.get("rejected_reasons") or []) or "invalid"
        emit(False, f"cert: fail — certificate REJECTED (proof invalid): {why}")
    emit(False, "cert: fail — valid proof but LEAK: " + "; ".join(v.get("leaks") or []))


if __name__ == "__main__":
    main()

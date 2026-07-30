#!/usr/bin/env python3
"""General honesty check logic: attest an agent's declared action-claims against
its own trusted `agent-stdio.log` trajectory. Engine-owned + importable; the
executable is a thin per-protocol wrapper (B9). Pure, stdlib-only.

Verdict: {"check","pass","feedback"}. Severity is NOT decided here — run-checks.py
stamps on_fail from the node's check entry (always `iterate` for honesty, B7).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actions  # noqa: E402  (sibling engine module)

CHECK = "claims-attested"
KNOWN_KINDS = {"command", "file_write"}
KNOWN_VERIFY = {"attest", "refetch"}


def _verdict(passed, feedback):
    return {"check": CHECK, "pass": passed, "feedback": feedback}


def _shape_error(claim):
    """Return a reason string if the claim is malformed, else ''."""
    if not isinstance(claim, dict):
        return "claim is not an object"
    if not claim.get("id"):
        return "claim missing `id`"
    if claim.get("kind") not in KNOWN_KINDS:
        return f"claim `{claim.get('id')}` has unknown kind {claim.get('kind')!r}"
    if claim.get("verify") not in KNOWN_VERIFY:
        return f"claim `{claim.get('id')}` has unknown verify {claim.get('verify')!r}"
    if not isinstance(claim.get("selector"), dict):
        return f"claim `{claim.get('id')}` missing selector object"
    return ""


def evaluate(evidence, trajectory, claims_required):
    """Attest each declared claim against `trajectory`.

    trajectory: list[record] (normalized log) | dict (error sentinel) | None.
    claims_required: list of claim ids the author demands (from params.claims_required).
    """
    evidence = evidence if isinstance(evidence, dict) else {}
    claims = evidence.get("claims")
    claims = claims if isinstance(claims, list) else []
    claims_required = claims_required or []

    # 1. Trajectory availability (B7 — unverified never passes).
    if trajectory is None:
        return _verdict(False, "trajectory unavailable (TRAJECTORY_PATH unset or unreadable)")
    if isinstance(trajectory, dict):
        return _verdict(False, f"trajectory artifact download failed: {trajectory.get('error', 'unknown')}")
    records = trajectory if isinstance(trajectory, list) else []

    # 2. Required claims must be declared, and must be attest-verified. The
    # agent controls `verify` in its own evidence; step 5 below skips every
    # `refetch` claim (it's the shipped C1 traces-exist-in-diff path, verified
    # by a SEPARATE check, never by claims-attested — design §3). Without this
    # gate, a required id declared `verify: refetch` would satisfy this
    # required-declared step and then sail through step 5's skip, netting
    # `pass: True` with zero attestation — exactly the bypass claims_required
    # exists to close.
    by_id = {c.get("id"): c for c in claims if isinstance(c, dict)}
    for rid in claims_required:
        if rid not in by_id:
            return _verdict(False, f"required claim `{rid}` was not declared in evidence.claims")
        if by_id[rid].get("verify") != "attest":
            return _verdict(False,
                             f"required claim `{rid}` must be verified by attestation "
                             f"(verify: \"attest\"), got verify={by_id[rid].get('verify')!r}")

    # 3. Shape-validate every declared claim.
    for c in claims:
        err = _shape_error(c)
        if err:
            return _verdict(False, err)

    # 4. Empty trajectory but a trajectory-dependent claim exists -> tamper
    # signature (§310). `refetch` claims never consult the trajectory (they're
    # the shipped C1 traces-exist-in-diff path), so they don't trigger this.
    needs_trajectory = [c for c in claims if isinstance(c, dict) and c.get("verify") != "refetch"]
    if needs_trajectory and not records:
        return _verdict(False, "trajectory present but empty (no recognized actions) — "
                               "stdio log missing/empty or all actions unrecognized")

    # 5. Attest each claim.
    for c in claims:
        if c.get("verify") == "refetch":
            continue  # refetch is the shipped C1 path (traces-exist-in-diff), not attested here
        m = actions.match_claim(records, c)
        if m["status"] == "none":
            return _verdict(False, f"claim `{c['id']}`: selector matched no action in the trajectory")
        if m["status"] == "ambiguous":
            return _verdict(False, f"claim `{c['id']}`: selector matched {m['count']} actions "
                                   f"(ambiguous — declare which, or set ambiguity)")
        record = m["record"]
        attested = c.get("attested") or {}
        if c["kind"] == "command":
            res = actions.attest_command(record, attested)
        else:
            res = actions.attest_file_write(record, attested)
        if not res["ok"]:
            return _verdict(False, f"claim `{c['id']}`: {res['reason']}")

    return _verdict(True, "")

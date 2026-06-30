#!/usr/bin/env python3
"""Form-check for a <leg>-judge (Revision 3 — lightened shape).

The judge echoes only `scope` + `gather_verdict`; this check:
  1. Re-derives scope independently from diff/PR_BODY (per mode) and asserts
     evidence.scope equals the recompute.
  2. Verifies gather_verdict is in the leg's valid enum.
  3. Checks scope-→-verdict consistency (e.g. !issue_linked ⇒ n/a).
  4. Checks grade form: each graded_finding has ref (non-empty) +
     severity in {blocking, advisory, noise}; examined is non-empty.

Per-leg dispatch via CHECK_PARAMS {"leg", "mode"}. Zone 3 — re-derives ground
truth, holds no creds.

ABI: judge-coverage.py <evidence.json> <diff.txt> <changed-files.txt>
Reads CHECK_PARAMS, PR_BODY, GITHUB_REPOSITORY, PR env.
Prints one {"check","pass","feedback"}. ALWAYS exit 0.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _locate  # noqa: E402
import _paths  # noqa: E402

NAME = "judge-coverage"
SEVS = {"blocking", "advisory", "noise"}

# Per-mode valid gather_verdict enums
_VERDICT_ENUMS = {
    "spec-solves":  {"solves", "does-not-solve", "n/a"},
    "plan-spec":    {"adheres", "underspec", "overspec", "n/a"},
    "code-plan":    {"adheres", "underplan", "overplan", "n/a"},
    "coherence":    {"adequate", "inadequate", "n/a"},
    "mm":           {"compliant", "diverges"},
    "security":     {"PASS", "LOCKED_VIOLATION", "n/a"},
}

# Whether the mode permits n/a (has a scope that can be out-of-range)
_NA_PERMITTED = {"spec-solves", "plan-spec", "code-plan", "coherence", "security"}

# For docs (coherence, applicable_without_code=True), n/a is NOT permitted
# even though the mode is "coherence". We detect this by the leg id.
_ALWAYS_APPLICABLE_LEGS = {"docs-updated-appropriately"}


def _emit(ok, fb):
    print(json.dumps({"check": NAME, "pass": ok, "feedback": fb}))


def _recompute_scope(mode, leg, diff_text, files, body):
    """Re-derive scope from diff+PR_BODY using the same _locate/_paths primitives
    as the gather checks. Returns a dict of bool flags for the mode's scope keys,
    or None for modes with no scope (mm, security)."""
    if mode == "spec-solves":
        issue_no = _locate.detect_issue_link(body)
        issue_linked = issue_no is not None
        spec_loc = _locate.locate("spec", body, files)
        spec_present = spec_loc["found"] and spec_loc["source"] in ("file", "body-section")
        return {"issue_linked": issue_linked, "spec_present": spec_present}

    if mode == "plan-spec":
        spec_loc = _locate.locate("spec", body, files)
        plan_loc = _locate.locate("plan", body, files)
        spec_present = spec_loc["found"] and spec_loc["source"] in ("file", "body-section")
        plan_present = plan_loc["found"] and plan_loc["source"] in ("file", "body-section")
        code_changed = any(_paths.is_code(p) for p in files)
        return {"spec_present": spec_present, "plan_present": plan_present, "code_changed": code_changed}

    if mode == "code-plan":
        plan_loc = _locate.locate("plan", body, files)
        plan_present = plan_loc["found"] and plan_loc["source"] in ("file", "body-section")
        code_changed = any(_paths.is_code(p) for p in files)
        return {"plan_present": plan_present, "code_changed": code_changed}

    if mode == "coherence":
        code_changed = any(_paths.is_code(p) for p in files)
        return {"code_changed": code_changed}

    # mm and security: no scope to recompute
    return None


def _check_scope_consistency(mode, leg, scope, gather_verdict):
    """Check that scope flags are consistent with gather_verdict.
    Returns (ok, feedback) where ok=True means consistent."""
    if mode == "spec-solves":
        if not scope.get("issue_linked") and gather_verdict != "n/a":
            return (False, f"spec-solves: !issue_linked but gather_verdict is {gather_verdict!r} (must be 'n/a')")

    if mode in ("plan-spec", "code-plan", "coherence"):
        if not scope.get("code_changed"):
            # No code changed → verdict must be n/a (for modes that allow it)
            # docs (coherence + always-applicable leg) never has n/a
            if leg in _ALWAYS_APPLICABLE_LEGS:
                # docs: n/a is never valid; pass consistency check (no constraint from !code_changed)
                pass
            else:
                if gather_verdict != "n/a":
                    return (False, f"{mode}: !code_changed but gather_verdict is {gather_verdict!r} (must be 'n/a')")

    return (True, "ok")


def main():
    try:
        params = json.loads(os.environ.get("CHECK_PARAMS", "") or "{}")
        mode = params.get("mode")
        leg = params.get("leg")
    except ValueError:
        mode = leg = None
    if not mode or not leg:
        _emit(False, "CHECK_PARAMS must carry {leg, mode}")
        return

    try:
        ev = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else {}
    except (OSError, ValueError) as exc:
        _emit(False, f"evidence unreadable: {exc}")
        return

    if not isinstance(ev, dict):
        _emit(False, "evidence must be a JSON object")
        return

    # Read inputs
    diff_text = open(sys.argv[2]).read() if len(sys.argv) > 2 else ""
    files = _paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else "")
    body = os.environ.get("PR_BODY", "") or ""

    # 1. validate gather_verdict enum
    valid_verdicts = _VERDICT_ENUMS.get(mode)
    if valid_verdicts is None:
        _emit(False, f"unknown mode {mode!r}")
        return

    gather_verdict = ev.get("gather_verdict")
    if gather_verdict not in valid_verdicts:
        _emit(False, f"gather_verdict {gather_verdict!r} not in valid enum for mode {mode!r}: {sorted(valid_verdicts)}")
        return

    # 2. scope re-derive and assert
    ev_scope = ev.get("scope")
    if not isinstance(ev_scope, dict):
        _emit(False, "evidence must have a 'scope' object (use {} for mm/security)")
        return

    recomputed = _recompute_scope(mode, leg, diff_text, files, body)
    if recomputed is not None:
        # Assert all scope keys match
        mismatches = []
        for key, expected_val in recomputed.items():
            agent_val = bool(ev_scope.get(key))
            if agent_val != expected_val:
                mismatches.append(f"{key}: agent={agent_val} recompute={expected_val}")
        if mismatches:
            _emit(False, f"scope disagreement: {'; '.join(mismatches)}")
            return
    # For mm/security (recomputed is None): scope accepted as-is (can be {})

    # 3. scope→verdict consistency
    ok, fb = _check_scope_consistency(mode, leg, ev_scope, gather_verdict)
    if not ok:
        _emit(False, fb)
        return

    # 4. docs mode: n/a not allowed (always applicable)
    if mode == "coherence" and leg in _ALWAYS_APPLICABLE_LEGS and gather_verdict == "n/a":
        _emit(False, f"coherence leg {leg!r} is always applicable; gather_verdict 'n/a' is not allowed")
        return

    # 5. examined must be non-empty
    examined = ev.get("examined")
    if not isinstance(examined, list) or not examined:
        _emit(False, "examined must be a non-empty list")
        return

    # 6. grade form: each graded_finding must have ref (non-empty) + valid severity
    graded = ev.get("graded_findings")
    if not isinstance(graded, list):
        _emit(False, "graded_findings must be an array")
        return
    for g in graded:
        if not isinstance(g, dict) or not g.get("ref") or g.get("severity") not in SEVS:
            _emit(False, "each graded finding needs {ref (non-empty), severity in blocking|advisory|noise}")
            return

    _emit(True, f"{leg} ({mode}): scope re-verified + gather_verdict={gather_verdict!r} + {len(graded)} findings graded.")


if __name__ == "__main__":
    main()

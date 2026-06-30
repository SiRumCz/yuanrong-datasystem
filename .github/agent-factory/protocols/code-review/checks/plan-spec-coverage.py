#!/usr/bin/env python3
"""Check: the plan-implements-spec leg's bidirectional matrix is complete, every
quote is verbatim in the self-fetched spec/plan text, the verdict is consistent
with the cells, and the leg's scope matches an independent recompute.

ABI: plan-spec-coverage.py <evidence.json> <diff.txt> <changed-files.txt>
Reads PR_BODY, GITHUB_REPOSITORY env; self-fetches spec/plan text at the PR head.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _artifact_fetch  # noqa: E402
import _diff  # noqa: E402
import _locate  # noqa: E402
import _paths  # noqa: E402

NAME = "plan-spec-coverage"


def _emit(ok, fb):
    print(json.dumps({"check": NAME, "pass": ok, "feedback": fb}))


def _verbatim(quote, text):
    """True iff the (whitespace-normalised) quote occurs in the text."""
    if quote is None:
        return True
    return _diff.norm(str(quote)) in _diff.norm(text or "")


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
        if not isinstance(ev, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        _emit(False, f"evidence unreadable / not JSON: {exc}")
        return

    body = os.environ.get("PR_BODY", "") or ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    ref = _artifact_fetch.head_sha(os.environ.get("PR", "")) or "HEAD"
    files = _paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else "")

    # --- independent scope recompute (committed-artifact only; no PR-desc fallback) ---
    spec_loc = _locate.locate("spec", body, files)
    plan_loc = _locate.locate("plan", body, files)
    spec_present = spec_loc["found"] and spec_loc["source"] in ("file", "body-section")
    plan_present = plan_loc["found"] and plan_loc["source"] in ("file", "body-section")
    code_changed = any(_paths.is_code(p) for p in files)

    scope = ev.get("scope") or {}
    a_code = bool(scope.get("code_changed"))
    a_spec = bool(scope.get("spec_present"))
    a_plan = bool(scope.get("plan_present"))
    if (a_code, a_spec, a_plan) != (code_changed, spec_present, plan_present):
        _emit(False, f"scope disagreement: agent={{'code':{a_code},'spec':{a_spec},'plan':{a_plan}}} "
                     f"recompute={{'code':{code_changed},'spec':{spec_present},'plan':{plan_present}}}")
        return

    verdict = ev.get("verdict")
    s2p = ev.get("spec_to_plan")
    p2s = ev.get("plan_to_spec")

    if not isinstance(s2p, list) or not isinstance(p2s, list):
        _emit(False, "spec_to_plan and plan_to_spec must both be arrays")
        return

    # --- verified N/A: out of scope (no code) + n/a + empty matrices ---
    if not code_changed:
        if verdict == "n/a" and not s2p and not p2s:
            _emit(True, "verified N/A (no code change; empty matrices).")
        else:
            _emit(False, "no code change but verdict is not n/a with empty matrices")
        return

    # --- code changed but the spec and/or plan artifact is absent: there is
    #     nothing to map, so empty matrices are the correct form. The block
    #     decision on a missing spec/plan belongs to conclude-preflight (it fires
    #     on the code_changed & !spec_present / !plan_present scope flags, which
    #     the recompute above already verified). Requiring a non-empty matrix here
    #     would make the leg un-passable on any PR without a committed spec/plan. ---
    if not spec_present or not plan_present:
        if not s2p and not p2s:
            _emit(True, f"verified absence (spec_present={spec_present}, "
                        f"plan_present={plan_present}); empty matrices.")
        else:
            _emit(False, "spec/plan absent but spec_to_plan/plan_to_spec must be empty")
        return

    # --- genuinely in-scope (code + spec + plan all present): matrices required ---
    if not s2p or not p2s:
        _emit(False, "in-scope leg must have non-empty spec_to_plan and plan_to_spec")
        return

    spec_text = _artifact_fetch.fetch_file_text(repo, spec_loc["changed_hits"][0], ref) if spec_loc["changed_hits"] else ""
    plan_text = _artifact_fetch.fetch_file_text(repo, plan_loc["changed_hits"][0], ref) if plan_loc["changed_hits"] else ""
    if spec_present and spec_text is None:
        _emit(False, "spec fetch failed (cannot verify quotes)")
        return
    if plan_present and plan_text is None:
        _emit(False, "plan fetch failed (cannot verify quotes)")
        return

    bad = []
    has_missing = False
    for cell in s2p:
        if not isinstance(cell, dict):
            bad.append("malformed spec_to_plan cell"); continue
        if not _verbatim(cell.get("requirement"), spec_text):
            bad.append(f"requirement not verbatim in spec: {cell.get('requirement')!r}")
        if cell.get("status") == "covered" and not _verbatim(cell.get("plan_quote"), plan_text):
            bad.append(f"plan_quote not verbatim in plan: {cell.get('plan_quote')!r}")
        if cell.get("status") == "missing":
            has_missing = True
    has_extra = False
    for cell in p2s:
        if not isinstance(cell, dict):
            bad.append("malformed plan_to_spec cell"); continue
        if not _verbatim(cell.get("plan_item"), plan_text):
            bad.append(f"plan_item not verbatim in plan: {cell.get('plan_item')!r}")
        if cell.get("status") == "traces" and not _verbatim(cell.get("spec_quote"), spec_text):
            bad.append(f"spec_quote not verbatim in spec: {cell.get('spec_quote')!r}")
        if cell.get("status") == "extra":
            has_extra = True

    # verdict consistency: underspec wins over overspec
    if has_missing:
        expected = "underspec"
    elif has_extra:
        expected = "overspec"
    else:
        expected = "adheres"
    if verdict != expected:
        bad.append(f"verdict {verdict!r} inconsistent with cells (expected {expected!r})")

    if bad:
        _emit(False, "; ".join(bad[:6]))
    else:
        _emit(True, f"matrix complete & consistent ({expected}).")


if __name__ == "__main__":
    main()

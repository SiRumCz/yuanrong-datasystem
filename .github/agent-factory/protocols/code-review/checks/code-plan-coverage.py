#!/usr/bin/env python3
"""Check: the code-implements-plan leg's plan-side matrix is complete, every
plan_item quote is verbatim in the self-fetched plan text, the verdict is
consistent with the cells, and scope matches an independent recompute.

The CODE side (files[].verdicts[].findings[] anchored to the diff) is validated
by the SEPARATE traces-exist-in-diff check wired on the same node — this check
does not re-validate diff anchors.

ABI: code-plan-coverage.py <evidence.json> <diff.txt> <changed-files.txt>
Reads PR_BODY, GITHUB_REPOSITORY env; self-fetches plan text at the PR head.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _artifact_fetch  # noqa: E402
import _diff  # noqa: E402
import _locate  # noqa: E402
import _paths  # noqa: E402

NAME = "code-plan-coverage"


def _emit(ok, fb):
    print(json.dumps({"check": NAME, "pass": ok, "feedback": fb}))


def _verbatim(quote, text):
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

    plan_loc = _locate.locate("plan", body, files)
    plan_present = plan_loc["found"] and plan_loc["source"] in ("file", "body-section")
    code_changed = any(_paths.is_code(p) for p in files)

    scope = ev.get("scope") or {}
    a_code = bool(scope.get("code_changed"))
    a_plan = bool(scope.get("plan_present"))
    if (a_code, a_plan) != (code_changed, plan_present):
        _emit(False, f"scope disagreement: agent={{'code':{a_code},'plan':{a_plan}}} "
                     f"recompute={{'code':{code_changed},'plan':{plan_present}}}")
        return

    verdict = ev.get("verdict")
    p2c = ev.get("plan_to_code")
    leg_files = ev.get("files")

    if not code_changed:
        if verdict == "n/a" and not p2c and not leg_files:
            _emit(True, "verified N/A (no code change; empty plan_to_code + files).")
        else:
            _emit(False, "no code change but verdict is not n/a with empty plan_to_code + files")
        return

    # --- code changed but the plan artifact is absent: there is no plan to map
    #     against, so an empty plan_to_code is the correct form. conclude-preflight
    #     owns the block on (code_changed & !plan_present), which the recompute
    #     above already verified. Requiring a non-empty matrix here would make the
    #     leg un-passable on any PR without a committed plan. ---
    if not plan_present:
        if not p2c:
            _emit(True, "verified absence (plan_present=False); empty plan_to_code.")
        else:
            _emit(False, "plan absent but plan_to_code must be empty")
        return

    if not isinstance(p2c, list) or not p2c:
        _emit(False, "in-scope leg must have a non-empty plan_to_code array")
        return

    plan_text = _artifact_fetch.fetch_file_text(repo, plan_loc["changed_hits"][0], ref) if plan_loc["changed_hits"] else ""
    if plan_present and plan_text is None:
        _emit(False, "plan fetch failed (cannot verify plan_item quotes)")
        return

    bad = []
    has_missing = False
    for cell in p2c:
        if not isinstance(cell, dict):
            bad.append("malformed plan_to_code cell"); continue
        if not _verbatim(cell.get("plan_item"), plan_text):
            bad.append(f"plan_item not verbatim in plan: {cell.get('plan_item')!r}")
        if cell.get("status") == "missing":
            has_missing = True

    # overplan signal: any finding that traces to no plan_item (null) or is flagged extra
    has_extra = False
    for entry in (leg_files or []):
        if not isinstance(entry, dict):
            continue
        for v in (entry.get("verdicts") or []):
            for f in (v.get("findings") or []):
                if isinstance(f, dict) and (f.get("plan_item") is None or f.get("status") == "extra"):
                    has_extra = True

    if has_missing:
        expected = "underplan"
    elif has_extra:
        expected = "overplan"
    else:
        expected = "adheres"
    if verdict != expected:
        bad.append(f"verdict {verdict!r} inconsistent with cells (expected {expected!r})")

    if bad:
        _emit(False, "; ".join(bad[:6]))
    else:
        _emit(True, f"plan_to_code complete & consistent ({expected}).")


if __name__ == "__main__":
    main()

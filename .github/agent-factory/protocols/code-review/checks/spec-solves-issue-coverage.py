#!/usr/bin/env python3
"""Check: the spec-solves-issue leg's coverage matrix accounts for every problem
the linked issue states, every addressed_by_spec spec_quote is verbatim in the
self-fetched spec text, the verdict is consistent, and scope matches recompute.

FAIL-CLOSED: when the issue is linked but the issue-body fetch fails, the check
FAILS with a distinct 'issue fetch failed' message (never silently treated as
"no problems" — that would fail-OPEN the presence gate on a private repo).

ABI: spec-solves-issue-coverage.py <evidence.json> <diff.txt> <changed-files.txt>
Reads PR_BODY, GITHUB_REPOSITORY env; self-fetches issue body + spec text.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _artifact_fetch  # noqa: E402
import _diff  # noqa: E402
import _locate  # noqa: E402

NAME = "spec-solves-issue-coverage"
# Problems in an issue body: lines starting "Problem:" or bullet items.
_PROBLEM = re.compile(r"^\s*(?:[-*]\s+|problem:\s*)(.+)$", re.I | re.M)


def _emit(ok, fb):
    print(json.dumps({"check": NAME, "pass": ok, "feedback": fb}))


def _verbatim(quote, text):
    if quote is None:
        return False
    return _diff.norm(str(quote)) in _diff.norm(text or "")


def _issue_problems(body):
    """Extract the issue's stated problems (one per 'Problem:'/bullet line)."""
    return [_diff.norm(m.group(1)) for m in _PROBLEM.finditer(body or "") if m.group(1).strip()]


def evaluate(ev, diff_text, changed_files, *, body, repo, pr):
    """Return (ok: bool, feedback: str). Core logic extracted for reuse by judge-coverage."""
    import _paths  # noqa: E402
    ref = _artifact_fetch.head_sha(pr) or "HEAD"
    files = changed_files

    # --- independent scope recompute ---
    issue_no = _locate.detect_issue_link(body)        # NEW _locate helper (int|None)
    issue_linked = issue_no is not None
    spec_loc = _locate.locate("spec", body, files)
    spec_present = spec_loc["found"] and spec_loc["source"] in ("file", "body-section")

    scope = ev.get("scope") or {}
    a_link = bool(scope.get("issue_linked"))
    a_spec = bool(scope.get("spec_present"))
    if (a_link, a_spec) != (issue_linked, spec_present):
        return (False, f"scope disagreement: agent={{'issue_linked':{a_link},'spec_present':{a_spec}}} "
                       f"recompute={{'issue_linked':{issue_linked},'spec_present':{spec_present}}}")

    verdict = ev.get("verdict")
    matrix = ev.get("matrix")

    # --- verified N/A: no linked issue + n/a + empty matrix ---
    if not issue_linked:
        if verdict == "n/a" and not matrix:
            return (True, "verified N/A (no linked issue; empty matrix).")
        else:
            return (False, "no linked issue but verdict is not n/a with empty matrix")

    # --- FAIL-CLOSED issue fetch ---
    issue = _artifact_fetch.fetch_issue(repo, issue_no)
    if not issue["ok"]:
        return (False, f"issue fetch failed for #{issue_no} (cannot verify coverage)")
    problems = _issue_problems(issue["body"])

    spec_text = _artifact_fetch.fetch_file_text(repo, spec_loc["changed_hits"][0], ref) if spec_loc["changed_hits"] else ""
    if spec_present and spec_text is None:
        return (False, "spec fetch failed (cannot verify spec quotes)")

    if not isinstance(matrix, list):
        return (False, "matrix must be an array")

    cell_problems = {_diff.norm(c.get("problem", "")) for c in matrix if isinstance(c, dict)}
    missing = [p for p in problems if p not in cell_problems]
    bad = []
    if missing:
        bad.append(f"problem(s) with no matrix cell: {missing[:3]}")
    has_unaddressed = False
    for c in matrix:
        if not isinstance(c, dict):
            bad.append("malformed matrix cell"); continue
        if c.get("status") == "addressed_by_spec":
            if not _verbatim(c.get("spec_quote"), spec_text):
                bad.append(f"spec_quote not verbatim in spec: {c.get('spec_quote')!r}")
        elif c.get("status") == "not_addressed":
            has_unaddressed = True
        else:
            bad.append(f"illegal cell status: {c.get('status')!r}")

    expected = "does-not-solve" if has_unaddressed else "solves"
    if verdict != expected:
        bad.append(f"verdict {verdict!r} inconsistent with cells (expected {expected!r})")

    if bad:
        return (False, "; ".join(bad[:6]))
    else:
        return (True, f"issue coverage complete & consistent ({expected}).")


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
    pr = os.environ.get("PR", "")
    diff_text = open(sys.argv[2]).read() if len(sys.argv) > 2 else ""
    import _paths  # noqa: E402
    files = _paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else "")
    ok, fb = evaluate(ev, diff_text, files, body=body, repo=repo, pr=pr)
    _emit(ok, fb)


if __name__ == "__main__":
    main()

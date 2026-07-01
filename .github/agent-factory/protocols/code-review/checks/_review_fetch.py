#!/usr/bin/env python3
"""Protocol-owned helper: fetch this PR's review activity for the
local-review-evidence check.

The checks job (engine zone 3) runs with a read-only token (GH_TOKEN, scoped
`pull-requests: read`) and run-checks.py forwards the job env to every check, so
a check can fetch its own ground truth here — the same way the checks job already
re-fetches `gh pr diff`. Keeping this in the protocol (not the engine) means the
generic engine never needs to know that code-review's preflight wants review
data; it stays a pure dispatcher.

Best-effort by design: any API/CLI failure yields empty arrays, so the *advisory*
local-review-evidence check degrades to its warn rather than erroring the run.

    fetch(repo, pr) -> {"reviewComments": [...], "reviews": [...], "issueComments": [...]}
"""
import json
import subprocess


def _api(path, jq):
    """gh api <path> --jq <jq>, returning the parsed list or [] on any failure."""
    try:
        out = subprocess.run(["gh", "api", path, "--jq", jq],
                             capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0 or not out.stdout.strip():
        return []
    try:
        return json.loads(out.stdout)
    except ValueError:
        return []


def fetch(repo, pr):
    """Return {reviewComments, reviews, issueComments} for <repo>#<pr>.

    repo is "owner/name" (e.g. GITHUB_REPOSITORY); pr is the PR number. Missing
    either → empty arrays (so the check falls back to the conversation-transcript
    / Reviewed-by signals it derives locally)."""
    if not repo or not pr:
        return {"reviewComments": [], "reviews": [], "issueComments": []}
    return {
        "reviewComments": _api(f"repos/{repo}/pulls/{pr}/comments",
                               "[.[]|{path,line,body,user:.user.login}]"),
        "reviews": _api(f"repos/{repo}/pulls/{pr}/reviews",
                        "[.[]|{state,body,user:.user.login}]"),
        "issueComments": _api(f"repos/{repo}/issues/{pr}/comments",
                              "[.[]|{body,user:.user.login}]"),
    }

#!/usr/bin/env python3
"""Prefetch PR review activity for the advisory local-review-evidence preflight
check (custody parity). Runs in the trusted checks job (has GH_TOKEN); the checks
themselves hold no creds, so review data is fetched here and forwarded via the
PR_REVIEW_JSON env var (mirrors the PR_BODY/PR_TITLE passthrough).

Prints ONE JSON line: {reviewComments, reviews, issueComments}. Best-effort —
any API failure yields empty arrays, so the check degrades to its advisory warn
rather than erroring the run. The conversation-transcript and Reviewed-by signals
are derived by the check itself (from changed-files and PR_BODY).

Usage: prefetch-review.py <owner/repo> <pr-number>
"""
import json
import subprocess
import sys


def api(path, jq):
    out = subprocess.run(["gh", "api", path, "--jq", jq], capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        return []
    try:
        return json.loads(out.stdout)
    except ValueError:
        return []


def main():
    if len(sys.argv) < 3 or not sys.argv[1] or not sys.argv[2]:
        print("{}")
        return
    repo, pr = sys.argv[1], sys.argv[2]
    print(json.dumps({
        "reviewComments": api(f"repos/{repo}/pulls/{pr}/comments", "[.[]|{path,line,body,user:.user.login}]"),
        "reviews": api(f"repos/{repo}/pulls/{pr}/reviews", "[.[]|{state,body,user:.user.login}]"),
        "issueComments": api(f"repos/{repo}/issues/{pr}/comments", "[.[]|{body,user:.user.login}]"),
    }))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Shared PR-review publication mechanism for code-review review branches.

Imported by the thin per-branch entrypoints (publish-grumpy.py,
publish-security.py), not invoked directly. Each entrypoint supplies its own
APPROVE / REQUEST_CHANGES wording and calls run().
"""
import json
import os
import subprocess
import sys


def gh_api(path, method=None, input_json=None, token=None, jq=None):
    """Thin wrapper over `gh api`; returns the completed subprocess."""
    cmd = ["gh", "api", path]
    if jq:
        cmd += ["--jq", jq]
    if method:
        cmd += ["--method", method, "--input", "-"]
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
    return subprocess.run(cmd, input=input_json, text=True, capture_output=True, env=env)


def _iter_verdicts(evidence):
    """Yield (file_path, verdict) for every verdict in the evidence."""
    for file_entry in evidence.get("files", []):
        for verdict in file_entry.get("verdicts", []):
            yield file_entry["path"], verdict


def _collect_comments(evidence):
    """Build the inline review comments from every issues-found finding.

    Each finding becomes one GitHub review comment anchored to a diff line.
    A finding with a `start_line` spans a range (start_line..line); otherwise
    it is a single-line comment.
    """
    comments = []
    for path, verdict in _iter_verdicts(evidence):
        if verdict.get("verdict") != "issues-found":
            continue
        for finding in verdict.get("findings", []):
            comment = {
                "path": path,
                "side": finding["side"],
                "line": finding["line"],
                "body": finding["comment"],
            }
            if finding.get("start_line"):
                comment["start_line"] = finding["start_line"]
                comment["start_side"] = finding["side"]
            comments.append(comment)
    return comments


def _submit_review(repo, pr, token, payload, event):
    """POST the review to GitHub; exit(1) if it cannot be submitted.

    A repo that forbids self-approval rejects an APPROVE event, so we retry
    the same payload once as a plain COMMENT before giving up.
    """
    def post(body):
        result = gh_api(f"repos/{repo}/pulls/{pr}/reviews", method="POST",
                        input_json=json.dumps(body), token=token)
        if result.returncode != 0:
            sys.stderr.write(f"[publish] reviews POST failed: {result.stdout}{result.stderr}\n")
        return result.returncode == 0

    if post(payload):
        return
    if event == "APPROVE":
        sys.stderr.write("[publish] APPROVE rejected (repo setting?); falling back to COMMENT\n")
        payload["event"] = "COMMENT"
        if post(payload):
            return
        sys.stderr.write("[publish] COMMENT fallback also failed\n")
    else:
        sys.stderr.write(f"[publish] review submission failed for event={event}\n")
    sys.exit(1)


def run(req_body, req_summary, ok_body, ok_summary):
    """Publish a PR review built from the evidence file named in sys.argv[1].

    req_*: REQUEST_CHANGES wording (req_body may contain {n}/{nfiles} placeholders).
    ok_* : APPROVE wording.
    Prints {"conclusion","summary"} to stdout for the engine to consume.
    """
    with open(sys.argv[1]) as f:
        evidence = json.load(f)

    # Any single issues-found verdict makes the whole branch request changes.
    has_issues = any(verdict.get("verdict") == "issues-found"
                     for _, verdict in _iter_verdicts(evidence))
    event = "REQUEST_CHANGES" if has_issues else "APPROVE"

    comments = _collect_comments(evidence)
    n = len(comments)
    nfiles = len({c["path"] for c in comments})

    if event == "REQUEST_CHANGES":
        body, summary, conclusion = req_body.format(n=n, nfiles=nfiles), req_summary, "failure"
    else:
        body, summary, conclusion = ok_body, ok_summary, "success"

    review = {"event": event, "body": body, "comments": comments}

    repo = os.environ["GITHUB_REPOSITORY"]
    pr = os.environ["PR"]
    token = os.environ.get("PUBLISH_TOKEN", "")

    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        # Dry-run: print the would-be POST instead of calling the API.
        sys.stderr.write(f"[ENGINE_LOCAL] POST repos/{repo}/pulls/{pr}/reviews\n")
        sys.stderr.write(json.dumps(review, indent=2) + "\n")
    else:
        commit = gh_api(f"repos/{repo}/pulls/{pr}", token=token, jq=".head.sha").stdout.strip()
        _submit_review(repo, pr, token, {**review, "commit_id": commit}, event)

    print(json.dumps({"conclusion": conclusion, "summary": summary}))

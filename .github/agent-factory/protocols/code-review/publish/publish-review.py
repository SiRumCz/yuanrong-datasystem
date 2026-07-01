#!/usr/bin/env python3
"""Publish one GitHub PR review from top-level review evidence."""
import json
import os
import subprocess
import sys


def gh_api(path, method=None, input_json=None, token=None, jq=None):
    cmd = ["gh", "api", path]
    if jq:
        cmd += ["--jq", jq]
    if method:
        cmd += ["--method", method, "--input", "-"]
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
    return subprocess.run(cmd, input=input_json, text=True, capture_output=True, env=env)


def _event(verdict):
    if verdict == "REQUEST_CHANGES":
        return "REQUEST_CHANGES"
    if verdict == "APPROVE":
        return "APPROVE"
    return "COMMENT"


def _conclusion(event):
    if event == "REQUEST_CHANGES":
        return "failure"
    if event == "APPROVE":
        return "success"
    return "neutral"


def _comment_body(finding):
    sev = finding.get("severity") or "unknown"
    title = finding.get("title") or "Review finding"
    impact = finding.get("impact") or ""
    fix = finding.get("fix") or ""
    return (
        f"**[{sev}] {title}**\n\n"
        "<details><summary>Impact and fix</summary>\n\n"
        f"Impact:\n{impact}\n\n"
        f"Fix:\n{fix}\n"
        "</details>"
    )


def _comments(evidence):
    out = []
    for finding in evidence.get("findings") or []:
        comment = {
            "path": finding["path"],
            "line": finding["line"],
            "side": "RIGHT",
            "body": _comment_body(finding),
        }
        if finding.get("start_line"):
            comment["start_line"] = finding["start_line"]
            comment["start_side"] = "RIGHT"
        out.append(comment)
    return out


def _body(evidence, event, ncomments):
    dim = evidence.get("dimension") or "review"
    if event == "REQUEST_CHANGES":
        return (
            f"{dim} review requests changes: {ncomments} finding(s) "
            "passed deterministic shape and anchor checks."
        )
    if event == "APPROVE":
        return f"{dim} review approves: no actionable findings."
    return f"{dim} review comment: {ncomments} non-blocking finding(s)."


def _submit_review(repo, pr, token, payload, event):
    def post(body):
        result = gh_api(
            f"repos/{repo}/pulls/{pr}/reviews",
            method="POST",
            input_json=json.dumps(body),
            token=token,
        )
        if result.returncode != 0:
            sys.stderr.write(f"[publish] reviews POST failed: {result.stdout}{result.stderr}\n")
        return result.returncode == 0

    if post(payload):
        return
    if event == "APPROVE":
        sys.stderr.write("[publish] APPROVE rejected; falling back to COMMENT\n")
        payload["event"] = "COMMENT"
        if post(payload):
            return
    sys.stderr.write(f"[publish] review submission failed for event={event}\n")
    sys.exit(1)


def main():
    with open(sys.argv[1]) as fh:
        evidence = json.load(fh)

    event = _event(evidence.get("verdict"))
    comments = _comments(evidence)
    payload = {
        "event": event,
        "body": _body(evidence, event, len(comments)),
        "comments": comments,
    }

    repo = os.environ["GITHUB_REPOSITORY"]
    pr = os.environ["PR"]
    token = os.environ.get("PUBLISH_TOKEN", "")
    head = os.environ.get("HEAD_SHA") or os.environ.get("PR_HEAD_SHA", "")

    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        payload["commit_id"] = head
        out = os.environ.get("REVIEW_POST_OUT")
        if out:
            with open(out, "w") as fh:
                json.dump(payload, fh)
        else:
            sys.stderr.write(json.dumps(payload, indent=2) + "\n")
    else:
        commit = head or gh_api(
            f"repos/{repo}/pulls/{pr}", token=token, jq=".head.sha"
        ).stdout.strip()
        _submit_review(repo, pr, token, {**payload, "commit_id": commit}, event)

    print(
        json.dumps(
            {
                "conclusion": _conclusion(event),
                "summary": _body(evidence, event, len(comments)),
            }
        )
    )


if __name__ == "__main__":
    main()

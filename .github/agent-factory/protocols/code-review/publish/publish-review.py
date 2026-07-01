#!/usr/bin/env python3
"""Publish one labeled GitHub issue per finding from review evidence."""
import json
import os
import subprocess
import sys

AI_REVIEW_LABEL = "ai-review"


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


def _label(dim):
    return f"review:{dim}"


def _title(dim, f):
    # prefix FIRST so conclude-fix._close_issues endswith(finding.title) matches
    return f"[ai-review][{dim}] " + (f.get("title") or "")


def _issue_body(f, dim, pr):
    return (f"`{f.get('path')}:{f.get('line')}` · **{f.get('severity') or 'unknown'}**\n\n"
            f"{f.get('impact') or ''}\n\n"
            f"**Suggested fix**\n```\n{f.get('fix') or ''}\n```\n\n"
            f"Found by the {dim} reviewer on PR #{pr}")


def _existing_titles(repo, dim, token):
    r = gh_api(f"repos/{repo}/issues?state=open&labels={_label(dim)}&per_page=100",
               token=token, jq=".[].title")
    if r.returncode != 0:
        return set()
    return set(t.strip() for t in (r.stdout or "").splitlines() if t.strip())


def _issue_plan(evidence, pr):
    dim = evidence.get("dimension") or "review"
    plan = []
    for f in (evidence.get("findings") or [])[:5]:
        plan.append({"title": _title(dim, f),
                     "labels": [AI_REVIEW_LABEL, _label(dim)],
                     "body": _issue_body(f, dim, pr)})
    return dim, plan


def _open_issues(plan, repo, token):
    opened = 0
    for item in plan:
        res = gh_api(f"repos/{repo}/issues", method="POST",
                     input_json=json.dumps({"title": item["title"], "body": item["body"],
                                            "labels": item["labels"]}), token=token)
        if res.returncode == 0:
            opened += 1
        else:
            sys.stderr.write(f"[publish-review] issue create failed: {res.stderr}\n")
    return opened


def main():
    evidence = json.load(open(sys.argv[1]))
    instance = sys.argv[2] if len(sys.argv) > 2 else ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr = os.environ.get("PR", "")
    token = os.environ.get("PUBLISH_TOKEN", "")
    event = _event(evidence.get("verdict"))
    dim, plan = _issue_plan(evidence, pr)

    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        out = os.environ.get("REVIEW_ISSUES_OUT", "")
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(plan, fh)
        else:
            sys.stderr.write("[ENGINE_LOCAL] review issue plan: " + json.dumps(plan) + "\n")
        opened = 0
    else:
        existing = _existing_titles(repo, dim, token)
        plan = [p for p in plan if p["title"].strip() not in existing]
        opened = _open_issues(plan, repo, token)

    print(json.dumps({"conclusion": _conclusion(event),
                      "summary": f"{dim}: {event}; opened {opened} issue(s)"}))


if __name__ == "__main__":
    main()

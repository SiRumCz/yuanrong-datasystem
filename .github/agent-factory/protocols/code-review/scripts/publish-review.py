#!/usr/bin/env python3
"""Publish one labeled GitHub issue per finding from review evidence.

The TERMINAL step of every `review` dimension leg (a `code` node, BPMN Script
Task, zone 4). ABI: <workdir> <instance-key>, with the dimension agent's
evidence materialized at <workdir>/inputs/evidence.json by the declared
`inputs: [{"from": "review", "as": "evidence"}]`.

Env is a least-privilege allowlist, so the node declares `env: ["PUBLISH_TOKEN"]`
— the default GITHUB_TOKEN cannot open the [ai-review] issues.
"""
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
    """The dimension leg's verdict -> the check-run colour.

    COMMENT maps to "success", NOT "neutral". A review that completed with
    non-blocking observations SUCCEEDED — COMMENT is a first-class verdict in
    review.evidence.schema.json's enum and the agents are told to emit it for
    "non-blocking observations only".

    "neutral" was correct under the retired `publish` slot, where the engine
    only reddened on "failure". As a `code` node this value is a real verdict:
    lib.finalize_code_result fails ANY conclusion outside {success, failure}
    closed, so returning "neutral" here publishes a RED check-run whose summary
    reads "verdict hook failed — blocked: <dim>: COMMENT" — an engine-crash
    message for a healthy review."""
    if event == "REQUEST_CHANGES":
        return "failure"
    return "success"


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


def _load_evidence(argv):
    """The dimension agent's evidence, from <workdir>/inputs/evidence.json.

    A bare path is also accepted so the hook stays directly invocable (the unit
    tests exercise it that way, and a human debugging a leg can point it at one
    evidence file)."""
    arg = argv[1] if len(argv) > 1 else ""
    staged = os.path.join(arg, "inputs", "evidence.json")
    path = staged if os.path.isfile(staged) else arg
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def main():
    evidence = _load_evidence(sys.argv)
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

    # This node is the LEG'S LAST STEP, so its stdout IS the leg's output: pass
    # the agent's evidence THROUGH, or `{"from": "<leg>"}` consumers (and the
    # from_fork rows the per-issue expander reads) see only the verdict.
    # `dimension`/`verdict`/`findings` are what downstream reads.
    #
    # `conclusion` is what colours the leg. On REQUEST_CHANGES it is "failure",
    # and `lib.finalize_code_result` maps an EXPLICIT conclusion to the step's
    # exit status ahead of the raw exit code — so the dimension's check-run goes
    # RED without this process exiting nonzero. That distinction is load-bearing
    # here, not stylistic: `lib.run_code_hook` DISCARDS stdout on a nonzero exit
    # (it returns {exit, summary-from-stderr} instead), which would throw away
    # the pass-through above and leave the leg's output evidence empty. Exit 0 +
    # an explicit failure conclusion is the one combination that gets both, and
    # is the shape code-review-ocr's post-review already uses.
    #
    # Red here does NOT stop the run: the leg declares no `on_blocked`, so the
    # fixer still gets to run (4.0.0's "colour is not flow").
    print(json.dumps({**evidence,
                      "conclusion": _conclusion(event),
                      "summary": f"{dim}: {event}; opened {opened} issue(s)"}))


if __name__ == "__main__":
    main()

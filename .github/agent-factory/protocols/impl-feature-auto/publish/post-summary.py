#!/usr/bin/env python3
"""post-summary (zone 4) — after implement's checks pass, resolve the PR by
pr_branch and comment on the originating issue with a link. ENGINE_LOCAL=1 does no
GitHub I/O. ABI: post-summary.py <evidence.json> <instance-key>; env ENGINE_LOCAL,
GITHUB_REPOSITORY, PUBLISH_TOKEN, PR. Prints {"conclusion","summary"}.

This hook only summarises/links — the PR WRITE is gh-aw safe-outputs (implement)."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "engine"))
import lib  # noqa: E402


def _local():
    return os.environ.get("ENGINE_LOCAL", "0") == "1"


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    instance = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        with open(ev_path) as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        ev = {}
    pr_branch = (ev.get("pr_branch") or "").strip()
    issue = lib.pr_from_instance(instance)

    if not pr_branch:
        print(json.dumps({"conclusion": "neutral",
                          "summary": "implement produced no pr_branch; no PR to link."}))
        return

    if _local():
        print(json.dumps({"conclusion": "success",
                          "summary": f"[local] would link PR on branch {pr_branch} to {instance}."}))
        return

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    env = dict(os.environ)
    env["GH_TOKEN"] = os.environ.get("PUBLISH_TOKEN", os.environ.get("GH_TOKEN", ""))
    summary = (ev.get("summary", "") or "").strip()

    def _gh_json(args):
        r = subprocess.run(["gh", *args], text=True, capture_output=True, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except ValueError:
                return None
        return None

    # gh-aw's safe-outputs appends a "-<hash>" suffix to the head branch it opens
    # (e.g. impl-feature-auto/issue-7-ab12…), so match by PREFIX, not an exact
    # --head lookup.
    prs = _gh_json(["pr", "list", "--repo", repo, "--state", "all",
                    "--json", "number,url,headRefName", "--limit", "40"]) or []
    match = next((p for p in prs
                  if str(p.get("headRefName", "")).startswith(pr_branch)), None)
    if match:
        pr_num, pr_url = match.get("number"), match.get("url")
        if str(issue).isdigit():
            lib.post_pr_comment(issue,
                                f"🤖 **Feature implemented** — opened PR #{pr_num}: {pr_url}"
                                + (f"\n\n{summary}" if summary else ""))
        print(json.dumps({"conclusion": "success",
                          "summary": f"Linked PR #{pr_num} for issue {instance}."}))
        return

    # No PR matched. gh-aw routes a change that touches protected paths (e.g. under
    # .github/) to a request_review *review issue* rather than an auto-PR, and may
    # push the branch without opening a PR. Confirm the branch exists (prefix) and
    # report honestly — the implementation was produced; a human-review artifact
    # (review issue / branch) carries it — rather than a false failure.
    refs = _gh_json(["api", f"repos/{repo}/git/matching-refs/heads/{pr_branch}"]) or []
    branch = next((r.get("ref", "").split("refs/heads/", 1)[-1]
                   for r in refs if r.get("ref")), "")
    if branch:
        if str(issue).isdigit():
            lib.post_pr_comment(issue,
                                f"🤖 **Feature implemented** — pushed to branch `{branch}` "
                                "and opened for review (changes touching protected paths "
                                "are routed to a review issue instead of an auto-PR)."
                                + (f"\n\n{summary}" if summary else ""))
        print(json.dumps({"conclusion": "neutral",
                          "summary": f"Implementation pushed to {branch} for issue {instance}; review pending."}))
        return

    print(json.dumps({"conclusion": "failure",
                      "summary": f"No PR or branch found for {pr_branch}."}))


if __name__ == "__main__":
    main()

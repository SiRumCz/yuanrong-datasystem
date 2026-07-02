#!/usr/bin/env python3
"""Conclude hook for fix: completeness against real triage input + suggestions."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _apply_fixes  # noqa: E402

CODE_FIXABLE = {"correctness", "security", "performance", "maintainability"}


def _load_json(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _triage_input():
    d = os.environ.get("CONCLUDE_INPUTS_DIR", "")
    if not d:
        return {}
    return _load_json(os.path.join(d, "triage.json"))


def _cluster_dims(cluster):
    dims = cluster.get("dimension") or cluster.get("dimensions") or []
    return {d for d in dims if isinstance(d, str)}


def _code_fixable_clusters(triage):
    out = []
    for cluster in triage.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        cid = cluster.get("cluster_id")
        if not cid:
            continue
        dims = _cluster_dims(cluster)
        if dims.intersection(CODE_FIXABLE):
            out.append(cluster)
    return out


def _classify(evidence, triage):
    fixes = evidence.get("fixes") if isinstance(evidence.get("fixes"), list) else []
    skipped = evidence.get("skipped") if isinstance(evidence.get("skipped"), list) else []
    triage_ids = {
        c.get("cluster_id")
        for c in (triage.get("clusters") or [])
        if isinstance(c, dict) and c.get("cluster_id")
    }
    fixable_ids = [c.get("cluster_id") for c in _code_fixable_clusters(triage)]
    fixed_ids = [f.get("cluster_id") for f in fixes if isinstance(f, dict) and f.get("cluster_id")]
    skipped_ids = [
        s.get("cluster_id") for s in skipped if isinstance(s, dict) and s.get("cluster_id")
    ]
    fixed_set = set(fixed_ids)
    skipped_set = set(skipped_ids)
    return {
        "mode": evidence.get("mode") or "suggest",
        "applied": [cid for cid in fixable_ids if cid in fixed_set],
        "skipped": [cid for cid in fixable_ids if cid in skipped_set],
        "dropped": [
            cid for cid in fixable_ids if cid not in fixed_set and cid not in skipped_set
        ],
        "unknown": {
            "fixes": sorted(cid for cid in fixed_set if cid not in triage_ids),
            "skipped": sorted(cid for cid in skipped_set if cid not in triage_ids),
        },
    }


def _suggestion_comments(evidence):
    comments = []
    for fix in evidence.get("fixes") or []:
        if not isinstance(fix, dict):
            continue
        comments.append(
            {
                "path": fix.get("path"),
                "line": fix.get("line"),
                "side": "RIGHT",
                "body": (
                    "```suggestion\n"
                    f"{fix.get('suggested_patch') or ''}\n"
                    "```\n\n"
                    f"{fix.get('rationale') or ''}"
                ),
            }
        )
    return comments


def _write_fix(report):
    out = os.environ.get("FIX_OUT", "/tmp/gh-aw/fix.json")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as fh:
            json.dump(report, fh)
    except OSError:
        pass


def _post_review(payload):
    out = os.environ.get("FIX_REVIEW_OUT")
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        if out:
            with open(out, "w") as fh:
                json.dump(payload, fh)
        else:
            sys.stderr.write(json.dumps(payload, indent=2) + "\n")
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr = os.environ.get("PR", "")
    if not repo or not pr:
        return
    env = dict(os.environ)
    if os.environ.get("PUBLISH_TOKEN"):
        env["GH_TOKEN"] = os.environ["PUBLISH_TOKEN"]
    subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr}/reviews", "--method", "POST", "--input", "-"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )


def _triage_clusters():
    return _triage_input().get("clusters") or []


def _issue_targets(applied_cluster_ids):
    """Map applied cluster_ids -> issue close-targets {label,title} via triage members."""
    targets = []
    seen = set()
    for cluster in _triage_clusters():
        if not isinstance(cluster, dict) or cluster.get("cluster_id") not in applied_cluster_ids:
            continue
        for m in cluster.get("member_findings") or []:
            if not isinstance(m, dict):
                continue
            dim, title = m.get("dimension"), m.get("title")
            if not dim or not title:
                continue
            key = (f"review:{dim}", title)
            if key not in seen:
                seen.add(key)
                targets.append({"label": key[0], "title": title})
    return targets


def _git(args, cwd, token=None):
    env = dict(os.environ)
    if token:
        env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", *args], cwd=cwd, env=env,
                          text=True, capture_output=True)


def _apply_commit_close(evidence):
    """Apply fixes to the PR head, push a commit, close resolved issues.
    Returns a report dict. ENGINE_LOCAL short-circuits to APPLY_WORKDIR/APPLY_OUT."""
    fixes = evidence.get("fixes") if isinstance(evidence.get("fixes"), list) else []
    report = {"applied": 0, "skipped": [], "pushed": False, "close": []}
    if not fixes:
        _write_apply(report)
        return report

    local = os.environ.get("ENGINE_LOCAL", "0") == "1"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr = os.environ.get("PR", "")
    token = os.environ.get("GH_TOKEN") or os.environ.get("PUBLISH_TOKEN")

    if local:
        workdir = os.environ.get("APPLY_WORKDIR")
        results = _apply_fixes.apply_all(workdir, fixes) if workdir else []
    else:
        if not repo or not pr or not token:
            report["diag"] = f"env missing: repo={bool(repo)} pr={bool(pr)} token={bool(token)}"
            _post_apply_comment(repo, pr, token, report)
            _write_apply(report)
            return report
        head = _pr_head_ref(repo, pr, token)
        if not head:
            report["diag"] = "pr_head_ref empty (gh pr view --json headRefName failed)"
            _post_apply_comment(repo, pr, token, report)
            _write_apply(report)
            return report
        workdir = tempfile.mkdtemp(prefix="fix-apply-")
        url = f"https://x-access-token:{token}@github.com/{repo}.git"
        cl = _git(["clone", "--depth", "1", "--branch", head, url, workdir])
        if cl.returncode != 0:
            shutil.rmtree(workdir, ignore_errors=True)
            report["diag"] = f"clone failed (branch={head}): {((cl.stderr or cl.stdout) or '').strip()[:200]}"
            _post_apply_comment(repo, pr, token, report)
            _write_apply(report)
            return report
        results = _apply_fixes.apply_all(workdir, fixes)

    try:
        applied = [r for r in results if r["status"] == "applied"]
        report["applied"] = len(applied)
        report["skipped"] = [
            {"cluster_id": r.get("cluster_id"), "path": r.get("path"), "reason": r.get("detail")}
            for r in results if r["status"] != "applied"
        ]
        report["close"] = _issue_targets({r["cluster_id"] for r in applied})

        if applied and not local:
            paths = sorted({r["path"] for r in applied})
            push = _commit_push(workdir, head, pr, paths, token)
            report["pushed"] = push["ok"]
            if not push["ok"]:
                report["push_error"] = push["detail"]
            if push["ok"]:
                _close_issues(repo, report["close"], token)
            shutil.rmtree(workdir, ignore_errors=True)
    except Exception as e:
        report["error"] = str(e)

    if not local:
        _post_apply_comment(repo, pr, token, report)
    _write_apply(report)
    return report


def _pr_head_ref(repo, pr, token):
    env = dict(os.environ); env["GH_TOKEN"] = token
    r = subprocess.run(["gh", "pr", "view", pr, "--repo", repo, "--json", "headRefName",
                        "--jq", ".headRefName"], text=True, capture_output=True, env=env)
    return r.stdout.strip() if r.returncode == 0 else ""


def _commit_push(workdir, head, pr, paths, token):
    """Commit the given paths and push to the PR head branch. Returns
    {"ok": bool, "detail": str} — detail carries the git error on failure."""
    paths = sorted(set(paths))
    if not paths:
        return {"ok": False, "detail": "no paths to commit"}
    _git(["config", "user.name", "agentic-fix-bot"], workdir)
    _git(["config", "user.email", "agentic-fix-bot@users.noreply.github.com"], workdir)
    _git(["add", "--", *paths], workdir)
    msg = f"fix: apply AI review remediations (PR #{pr})"
    c = _git(["commit", "-m", msg], workdir)
    if c.returncode != 0:
        return {"ok": False, "detail": f"commit failed: {(c.stderr or c.stdout).strip()[:300]}"}
    # Explicit refspec so the push targets the PR head branch unambiguously.
    p = _git(["push", "origin", f"HEAD:refs/heads/{head}"], workdir, token=token)
    if p.returncode != 0:
        return {"ok": False, "detail": f"push failed: {(p.stderr or p.stdout).strip()[:300]}"}
    return {"ok": True, "detail": ""}


def _post_apply_comment(repo, pr, token, report):
    """Surface the fix-apply outcome on the PR so failures are visible (never silent)."""
    if not repo or not pr:
        return
    lines = [f"AI fix phase: applied={report.get('applied', 0)}, pushed={report.get('pushed', False)}."]
    if report.get("diag"):
        lines.append(f"Diag: {report['diag']}")
    if report.get("push_error"):
        lines.append(f"Push error: {report['push_error']}")
    if report.get("error"):
        lines.append(f"Error: {report['error']}")
    skipped = report.get("skipped") or []
    if skipped:
        lines.append("Skipped fixes:")
        for s in skipped[:10]:
            lines.append(f"- {s.get('cluster_id')} ({s.get('path')}): {s.get('reason')}")
    body = "\n".join(lines)
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
    subprocess.run(["gh", "api", f"repos/{repo}/issues/{pr}/comments", "-f", f"body={body}"],
                   text=True, capture_output=True, env=env)


def _close_issues(repo, targets, token):
    env = dict(os.environ); env["GH_TOKEN"] = token
    for t in targets:
        label = t["label"]
        target_title = t["title"]
        # Reconstruct the full issue title as publish-review opens it:
        # "[ai-review][<dim>] <finding-title>"
        dim = label.split(":", 1)[1] if ":" in label else label
        expected_title = f"[ai-review][{dim}] {target_title}"
        listing = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--label", label,
             "--state", "open", "--json", "number,title"],
            text=True, capture_output=True, env=env)
        try:
            items = json.loads(listing.stdout or "[]")
        except ValueError:
            items = []
        for it in items:
            if (it.get("title") or "").strip() == expected_title.strip():
                subprocess.run(["gh", "issue", "close", str(it["number"]), "--repo", repo,
                                "--comment", "Resolved by the AI fix phase (committed to the PR)."],
                               text=True, capture_output=True, env=env)


def _write_apply(report):
    out = os.environ.get("APPLY_OUT")
    if not out:
        return
    try:
        with open(out, "w") as fh:
            json.dump(report, fh)
    except OSError:
        pass


def main():
    evidence = _load_json(sys.argv[1] if len(sys.argv) > 1 else "")
    triage = _triage_input()
    report = _classify(evidence, triage)
    _write_fix(report)
    comments = _suggestion_comments(evidence)
    payload = {
        "event": "COMMENT",
        "body": f"Fix suggestions: {len(comments)} suggestion(s).",
        "comments": comments,
        "commit_id": os.environ.get("HEAD_SHA") or os.environ.get("PR_HEAD_SHA", ""),
    }
    _post_review(payload)
    apply_report = _apply_commit_close(evidence)
    sys.stderr.write("[conclude-fix] APPLY_REPORT " + json.dumps(apply_report) + "\n")
    print(
        json.dumps(
            {
                "conclusion": "neutral",
                "summary": (
                    f"Fix suggestions: clusters_fixed={len(report['applied'])}, "
                    f"skipped={len(report['skipped'])}, dropped={len(report['dropped'])}, "
                    f"unknown={len(report['unknown']['fixes']) + len(report['unknown']['skipped'])}."
                    f" files_patched={apply_report['applied']}, pushed={apply_report['pushed']}"
                    f"{('; push_error=' + apply_report['push_error']) if apply_report.get('push_error') else ''}"
                    f"{('; error=' + apply_report['error']) if apply_report.get('error') else ''}."
                ),
                "blocked": False,
            }
        )
    )


if __name__ == "__main__":
    main()

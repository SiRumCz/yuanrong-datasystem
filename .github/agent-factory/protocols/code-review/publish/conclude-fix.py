#!/usr/bin/env python3
"""Conclude hook for fix: completeness against real triage input + suggestions."""
import json
import os
import subprocess
import sys

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
    print(
        json.dumps(
            {
                "conclusion": "neutral",
                "summary": (
                    f"Fix suggestions: applied={len(report['applied'])}, "
                    f"skipped={len(report['skipped'])}, dropped={len(report['dropped'])}, "
                    f"unknown={len(report['unknown']['fixes']) + len(report['unknown']['skipped'])}."
                ),
                "blocked": False,
            }
        )
    )


if __name__ == "__main__":
    main()

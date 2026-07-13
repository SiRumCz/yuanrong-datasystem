#!/usr/bin/env python3
"""
collect.py — read-only data collection for the PR #6 human-bottleneck analysis.

Pulls every discussion/decision artifact for SiRumCz/yuanrong-datasystem#6 (the
recreated LogSampler PR) and, for authentic backing, the upstream original it
recreates: gitcode openeuler/yuanrong-datasystem !1064 plus its linked RFC
issue #574.

Everything is READ-ONLY (GET only). Raw JSON is cached under ./data/ so the
report is fully reproducible offline and the exact inputs are recorded in the PR.

Usage:  python3 collect.py
Requires: `gh` authenticated for github.com (read); network for api.gitcode.com.
"""
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

GH_REPO = "SiRumCz/yuanrong-datasystem"
GH_PR = 6
GITCODE_API = "https://api.gitcode.com/api/v5/repos/openeuler/yuanrong-datasystem"
GITCODE_MR = 1064
GITCODE_RFC_ISSUE = 574


def save(name, obj):
    path = os.path.join(DATA, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    n = len(obj) if isinstance(obj, list) else 1
    print(f"  saved {name:32s} ({n} record{'s' if n != 1 else ''})")
    return obj


def gh(endpoint, paginate=True):
    """Call `gh api`. For arrays, paginate manually (?per_page=100&page=N) so we
    do not depend on `--slurp`/`--jq` (unavailable on older gh)."""
    if not paginate:
        out = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True)
        if out.returncode != 0:
            print(f"  !! gh api {endpoint} failed: {out.stderr.strip()[:200]}", file=sys.stderr)
            return {}
        return json.loads(out.stdout or "{}")
    results, page = [], 1
    while True:
        sep = "&" if "?" in endpoint else "?"
        ep = f"{endpoint}{sep}per_page=100&page={page}"
        out = subprocess.run(["gh", "api", ep], capture_output=True, text=True)
        if out.returncode != 0:
            print(f"  !! gh api {ep} failed: {out.stderr.strip()[:200]}", file=sys.stderr)
            break
        chunk = json.loads(out.stdout or "[]")
        if not isinstance(chunk, list) or not chunk:
            break
        results.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return results


def gitcode_paginated(path):
    """GET api.gitcode.com (Gitee v5 style) paginating ?page=&per_page=100."""
    results, page = [], 1
    while True:
        url = f"{GITCODE_API}{path}{'&' if '?' in path else '?'}page={page}&per_page=100"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pr6-analysis"})
            with urllib.request.urlopen(req, timeout=20) as r:
                chunk = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - record-and-continue for an analysis tool
            print(f"  !! gitcode {path} page {page} failed: {e}", file=sys.stderr)
            break
        if not isinstance(chunk, list) or not chunk:
            break
        results.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return results


def gitcode_one(path):
    url = f"{GITCODE_API}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pr6-analysis"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"  !! gitcode {path} failed: {e}", file=sys.stderr)
        return {}


def main():
    print(f"[1/2] SiRumCz {GH_REPO}#{GH_PR} (via gh api, read-only)")
    save("sirumcz_pr.json", gh(f"repos/{GH_REPO}/pulls/{GH_PR}", paginate=False))
    save("sirumcz_issue_comments.json", gh(f"repos/{GH_REPO}/issues/{GH_PR}/comments"))
    save("sirumcz_review_comments.json", gh(f"repos/{GH_REPO}/pulls/{GH_PR}/comments"))
    save("sirumcz_reviews.json", gh(f"repos/{GH_REPO}/pulls/{GH_PR}/reviews"))
    save("sirumcz_commits.json", gh(f"repos/{GH_REPO}/pulls/{GH_PR}/commits"))
    save("sirumcz_files.json", gh(f"repos/{GH_REPO}/pulls/{GH_PR}/files"))

    print(f"[2/2] Upstream gitcode openeuler/yuanrong-datasystem !{GITCODE_MR} (+ RFC #{GITCODE_RFC_ISSUE})")
    save("gitcode_mr.json", gitcode_one(f"/pulls/{GITCODE_MR}"))
    save("gitcode_mr_comments.json", gitcode_paginated(f"/pulls/{GITCODE_MR}/comments"))
    save("gitcode_rfc_issue.json", gitcode_one(f"/issues/{GITCODE_RFC_ISSUE}"))
    save("gitcode_rfc_comments.json", gitcode_paginated(f"/issues/{GITCODE_RFC_ISSUE}/comments"))

    print("done. raw inputs cached under ./data/")


if __name__ == "__main__":
    main()

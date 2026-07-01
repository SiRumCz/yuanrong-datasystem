#!/usr/bin/env python3
"""Protocol-owned self-fetch helper for the preflight coverage checks.

Mirrors _review_fetch.py: a zone-3 check fetches its own ground truth with the
checks job's read-only token (gh on PATH), so the engine prefetches nothing
protocol-specific. Two artifacts the adherence-chain checks need:

  fetch_issue(repo, number) -> {"ok", "body"}   # the linked issue's body text
  fetch_file_text(repo, path, ref) -> str|None  # a committed file at the PR head

fetch_issue returns an explicit ok flag so a coverage check can FAIL-CLOSED:
"issue fetch failed" (ok=False) must be distinguishable from "issue text has no
match" (ok=True, real verdict) — collapsing both would fail-OPEN a presence gate
on a private repo.
"""
import base64
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _locate  # noqa: E402  (ARTIFACT_MAX_CHARS — one cap shared with the agent prefetch)


def _run(args):
    try:
        return subprocess.run(["gh", *args], capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None


def fetch_issue(repo, number):
    """Fetch issue <number>'s body. {"ok": False} on any failure (fail-closed)."""
    if not repo or not number:
        return {"ok": False, "body": ""}
    out = _run(["api", f"repos/{repo}/issues/{number}", "--jq", ".body"])
    if out is None or out.returncode != 0:
        return {"ok": False, "body": ""}
    return {"ok": True, "body": (out.stdout or "")[: _locate.ARTIFACT_MAX_CHARS]}


def fetch_file_text(repo, path, ref):
    """Fetch a committed file's text at <ref>. None on any failure."""
    if not repo or not path:
        return None
    ref = ref or "HEAD"
    out = _run(["api", f"repos/{repo}/contents/{path}?ref={ref}", "--jq", ".content"])
    if out is None or out.returncode != 0 or not (out.stdout or "").strip():
        return None
    try:
        return base64.b64decode(out.stdout.strip()).decode("utf-8")[: _locate.ARTIFACT_MAX_CHARS]
    except Exception:
        return None


def head_sha(pr):
    """The PR's head commit SHA via `gh pr view <pr> --json headRefOid`, or "".

    The checks job checks out the DEFAULT branch and the engine exports no head
    SHA, so fetch_file_text MUST read the PR head explicitly — otherwise it reads
    a committed-but-unchanged spec/plan from the wrong ref. Each coverage check
    derives the ref via this helper:  ref = head_sha(PR) or "HEAD".
    """
    if not pr:
        return ""
    out = _run(["pr", "view", str(pr), "--json", "headRefOid", "--jq", ".headRefOid"])
    if out is None or out.returncode != 0:
        return ""
    return (out.stdout or "").strip()

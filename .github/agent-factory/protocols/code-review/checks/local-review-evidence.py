#!/usr/bin/env python3
"""Check: evidence of a local review before merge — ports custody checks.js
localReviewEvidence. Advisory (protocol.json wires on_fail: advisory): absence is
a flag, never a hard block; the gate's point is to make the review TRACEABLE.

Two ways (custody's demo thesis):
  - yuanrong-way: PR review activity — line-anchored review comments, review
    submissions (COMMENTED/APPROVED/CHANGES_REQUESTED), or an approval (/lgtm,
    /approve). Fetched here via _review_fetch using the checks job's read-only
    token (GITHUB_REPOSITORY + PR env) — the same way the job re-fetches the diff;
    the engine never prefetches this, so it stays protocol-agnostic.
  - custody-way: a committed agent-conversation transcript for this PR
    (`.conversations/*.jsonl`, detected from the changed-files — this repo commits
    the transcript IN the PR head, cf. context phase), or a `Reviewed-by:` trailer
    in the PR body (read from PR_BODY).

Usage: local-review-evidence.py <evidence.json> <diff.txt> <changed-files.txt>;
reads GITHUB_REPOSITORY + PR (to fetch review activity) and PR_BODY env."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402
import _review_fetch  # noqa: E402

TRAILER = re.compile(r"(^|\n)\s*reviewed-by:\s*\S", re.I)
APPROVE = re.compile(r"(^|\s)/?(lgtm|approve)\b", re.I)
SUBMISSION = re.compile(r"COMMENTED|APPROVED|CHANGES_REQUESTED", re.I)
APPROVED = re.compile(r"APPROVED", re.I)
CONVERSATION = re.compile(r"(^|/)\.conversations/.+\.jsonl$", re.I)


def main():
    review = _review_fetch.fetch(os.environ.get("GITHUB_REPOSITORY", ""),
                                 os.environ.get("PR", ""))
    body = os.environ.get("PR_BODY", "") or ""
    files = _paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else "")

    review_comments = review.get("reviewComments") or []
    reviews = review.get("reviews") or []
    issue_comments = review.get("issueComments") or []
    conversation = any(CONVERSATION.search(f) for f in files)
    trailer = bool(TRAILER.search(body))

    findings = len(review_comments)
    submissions = sum(1 for v in reviews if SUBMISSION.search(v.get("state") or ""))
    approved = any(APPROVED.search(v.get("state") or "") for v in reviews) or \
        any(APPROVE.search(c.get("body") or "") for c in issue_comments)
    yuanrong = findings > 0 or submissions > 0 or approved

    if yuanrong or conversation or trailer:
        ways = "; ".join(w for w in [
            "PR review comments/approval" if yuanrong else "",
            "a captured agent conversation" if conversation else "",
            "a Reviewed-by trailer" if trailer else "",
        ] if w)
        print(json.dumps({"check": "local-review-evidence", "pass": True,
                          "feedback": f"Local review evidence found ({ways})."}))
    else:
        print(json.dumps({"check": "local-review-evidence", "pass": False,
                          "feedback": "No evidence of a local review before push — no PR review "
                                      "comments/approval, no captured agent conversation, no "
                                      "Reviewed-by trailer."}))


if __name__ == "__main__":
    main()

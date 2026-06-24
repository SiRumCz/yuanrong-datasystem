#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import _review
_review.run(
    req_body="\U0001f512 Security review — {n} potential issue(s) across {nfiles} file(s), "
             "evidence verified by deterministic checks. Details inline.",
    req_summary="Security review flagged issues — resolve them before merging. See the inline comments.",
    ok_body="\U0001f512 Security review — examined the changed surface and found no vulnerabilities worth flagging.",
    ok_summary="Security review found nothing to fix.")

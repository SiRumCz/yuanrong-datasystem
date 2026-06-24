#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import _review
_review.run(
    req_body="\U0001f624 Grumpy protocol review — {n} issue(s) across {nfiles} file(s), "
             "evidence verified by deterministic checks. Griping inline.",
    req_summary="Grumpy requested changes — resolve them before merging. See the inline comments.",
    ok_body="\U0001f624 Fine. I examined every file against every category and found nothing "
            "worth complaining about. Don't get used to it.",
    ok_summary="Grumpy examined every file × category and found nothing to fix.")

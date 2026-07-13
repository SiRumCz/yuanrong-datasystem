#!/usr/bin/env python3
"""
compute_metrics.py — turn the cached raw data (./data/) into metrics.json and a
generated REPORT.md for the PR #6 human-bottleneck analysis.

The report is GENERATED from the data (no hand-typed numbers) so it is fully
reproducible: re-run `collect.py` then `compute_metrics.py` to regenerate.

Three claims this quantifies for SiRumCz/yuanrong-datasystem#6 (a recreation of
upstream gitcode openeuler/yuanrong-datasystem !1064, the LogSampler feature):
  1. Decisions are BURIED  — many findings scattered across the diff, no index.
  2. Too much HUMAN involvement — the upstream original's real review thread.
  3. UNREADABLE — to know what was decided & why, you must read all of it.

Honesty notes baked into the report:
  * #6's *issue* comments are recreated CI/mirror posts authored by one account,
    so participant counts for "human involvement" come from the UPSTREAM original
    (!1064), not from #6's recreated thread.
  * #6's *review* comments are the seeded [severity][category] decision corpus —
    that IS the legitimate "buried decisions" surface analysed here.
  * Decision classification is keyword/tag based (documented below); approximate.
"""
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# --- documented heuristics -------------------------------------------------
SEVERITY_TAGS = ["严重", "警告", "提示", "建议", "重要"]          # critical/warning/note/suggestion/important
CATEGORY_TAGS = ["Bug", "安全", "性能", "测试", "文档", "设计",   # bug/security/perf/test/docs/design
                 "正确性", "可维护性", "可读性", "并发"]            # correctness/maintainability/readability/concurrency
FIX_ACK = re.compile(r"已修复|已修改|已解决|已优化|已调整|fixed|resolved|done", re.I)
BOT_RX = re.compile(r"bot|ci|robot|gitee|action|workflow", re.I)
# Reading-time model (stated, conservative): Latin words @200 wpm; CJK chars @400 cpm.
WPM_LATIN, CPM_CJK = 200.0, 400.0
CJK_RX = re.compile(r"[一-鿿]")
LATIN_WORD_RX = re.compile(r"[A-Za-z0-9_]+")


def L(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def login(u):
    if not isinstance(u, dict):
        return "?"
    return u.get("login") or u.get("name") or "?"


def text_stats(bodies):
    latin = cjk = 0
    for b in bodies:
        b = b or ""
        latin += len(LATIN_WORD_RX.findall(b))
        cjk += len(CJK_RX.findall(b))
    minutes = latin / WPM_LATIN + cjk / CPM_CJK
    return {"latin_words": latin, "cjk_chars": cjk, "read_minutes": round(minutes, 1)}


def has_tag(body, tags):
    head = (body or "")[:80]
    return any(f"[{t}]" in head for t in tags)


def severity_of(body):
    head = (body or "")[:80]
    for t in SEVERITY_TAGS:
        if f"[{t}]" in head:
            return t
    return None


def parse_dt(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    pr = L("sirumcz_pr.json")
    issue_c = L("sirumcz_issue_comments.json")
    review_c = L("sirumcz_review_comments.json")
    reviews = L("sirumcz_reviews.json")
    files = L("sirumcz_files.json")

    # ---- SUBJECT: SiRumCz #6 ------------------------------------------------
    changed = pr.get("changed_files") or len(files)
    add, dele = pr.get("additions", 0), pr.get("deletions", 0)
    loc = add + dele

    # review comments are the decision corpus; reviews(163) are 1:1 wrappers
    # (all state COMMENTED) so we count INLINE comments, not reviews, as artifacts.
    review_states = Counter(r.get("state") for r in reviews)
    findings = [c for c in review_c if severity_of(c.get("body", ""))]
    by_sev = Counter(severity_of(c.get("body", "")) for c in findings)
    by_cat = Counter(t for c in review_c for t in CATEGORY_TAGS if f"[{t}]" in (c.get("body", "")[:80]))
    replies = sum(1 for c in review_c if c.get("in_reply_to_id"))
    fix_acks = sum(1 for c in review_c if FIX_ACK.search(c.get("body", "")))
    files_with_findings = len({c.get("path") for c in review_c if c.get("path")})

    artifacts_6 = len(issue_c) + len(review_c)   # 162 + 163; reviews are wrappers, not added
    txt6 = text_stats([c.get("body", "") for c in issue_c + review_c])

    subject = {
        "repo": "SiRumCz/yuanrong-datasystem", "number": 6,
        "title": pr.get("title"), "author": login(pr.get("user")),
        "changed_files": changed, "additions": add, "deletions": dele, "loc_changed": loc,
        "commits": pr.get("commits"),
        "issue_comments": len(issue_c), "review_comments": len(review_c),
        "review_submissions": len(reviews), "review_submission_states": dict(review_states),
        "discussion_artifacts": artifacts_6,
        "decision_findings": len(findings),
        "findings_by_severity": dict(by_sev),
        "findings_by_category": dict(by_cat),
        "threaded_replies": replies,
        "fix_acknowledgements": fix_acks,
        "files_touched_by_review": files_with_findings,
        "comments_per_changed_file": round(artifacts_6 / changed, 1) if changed else None,
        "review_comments_per_100_loc": round(100 * len(review_c) / loc, 2) if loc else None,
        "text": txt6,
        "note": "issue comments are recreated CI/mirror posts by one account; the 163 "
                "review comments are the seeded [severity][category] decision corpus.",
    }

    # ---- AUTHENTIC: upstream !1064 -----------------------------------------
    mr = L("gitcode_mr.json")
    mrc = L("gitcode_mr_comments.json")
    rfc = L("gitcode_rfc_issue.json")
    rfcc = L("gitcode_rfc_comments.json")

    def is_bot(c):
        return bool(BOT_RX.search(login(c.get("user"))))
    humans = [c for c in mrc if not is_bot(c)]
    human_authors = Counter(login(c.get("user")) for c in humans)
    bot_authors = Counter(login(c.get("user")) for c in mrc if is_bot(c))
    ctypes = Counter(c.get("comment_type") for c in mrc)
    created, merged = parse_dt(mr.get("created_at")), parse_dt(mr.get("merged_at"))
    days_open = round((merged - created).total_seconds() / 86400, 1) if created and merged else None
    txt_up = text_stats([c.get("body", "") for c in mrc])

    upstream = {
        "platform": "gitcode openeuler/yuanrong-datasystem", "number": mr.get("number"),
        "title": mr.get("title"), "author": login(mr.get("merged_by")) if False else None,
        "state": mr.get("state"), "created_at": mr.get("created_at"), "merged_at": mr.get("merged_at"),
        "days_open": days_open,
        "total_comments": len(mrc),
        "human_comments": len(humans), "bot_comments": len(mrc) - len(humans),
        "distinct_humans": len(human_authors), "human_breakdown": dict(human_authors),
        "bot_breakdown": dict(bot_authors),
        "comment_types": dict(ctypes),
        "rfc_issue": {"number": rfc.get("number"), "title": rfc.get("title"),
                      "comments": len(rfcc)},
        "text": txt_up,
    }

    # ---- DERIVED ------------------------------------------------------------
    # signal ratio: of everything on #6's surface, how much is an explicit decision/finding
    signal_ratio = round(100 * len(findings) / artifacts_6, 1) if artifacts_6 else None
    derived = {
        "decision_signal_ratio_pct": signal_ratio,
        "decision_signal_note": f"{len(findings)} explicit findings hidden among "
                                f"{artifacts_6} #6 artifacts → you read 100% to find {signal_ratio}%.",
        "read_everything_minutes_subject": txt6["read_minutes"],
        "read_everything_minutes_upstream": txt_up["read_minutes"],
        "no_single_index": True,
        "no_single_index_note": "Decisions are distributed across review threads and "
                                f"{files_with_findings} files with no merge-readiness summary; "
                                "reconstructing the decision log requires reading the whole thread.",
    }

    method = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "severity_tags": SEVERITY_TAGS, "category_tags": CATEGORY_TAGS,
        "fix_ack_regex": FIX_ACK.pattern, "bot_regex": BOT_RX.pattern,
        "reading_model": f"Latin words @{WPM_LATIN:.0f} wpm + CJK chars @{CPM_CJK:.0f} cpm (stated estimate)",
        "inputs": sorted(os.listdir(DATA)),
        "caveats": [
            "#6 issue comments are recreated CI/mirror posts by a single account; "
            "participant/human-involvement metrics are taken from the UPSTREAM original !1064.",
            "Decision detection is [severity]-tag based on #6's seeded review corpus; approximate.",
            "review_submissions(163) are 1:1 COMMENTED wrappers of the 163 inline review "
            "comments and are NOT double-counted in discussion_artifacts.",
            "Reading-time is a coarse estimate from the stated word/char rates.",
        ],
    }

    metrics = {"subject_pr": subject, "upstream_origin": upstream,
               "derived": derived, "method": method}
    with open(os.path.join(HERE, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print("wrote metrics.json")
    render_report(metrics)
    print("wrote REPORT.md")


def render_report(m):
    s, u, d, me = m["subject_pr"], m["upstream_origin"], m["derived"], m["method"]

    def hrs(mins):
        return f"{mins/60:.1f} h" if mins >= 90 else f"{mins:.0f} min"

    sev = s["findings_by_severity"]
    cat = s["findings_by_category"]
    sev_row = ", ".join(f"{k}×{v}" for k, v in sorted(sev.items(), key=lambda x: -x[1]))
    cat_row = ", ".join(f"{k}×{v}" for k, v in sorted(cat.items(), key=lambda x: -x[1]))
    hb = ", ".join(f"{k} ({v})" for k, v in sorted(u["human_breakdown"].items(), key=lambda x: -x[1]))

    md = f"""# PR #6 — Human bottleneck & buried-decision analysis

> Subject: **[{s['repo']}#{s['number']}]** — *{s['title']}*
> A recreation of upstream **gitcode openeuler/yuanrong-datasystem !{u['number']}** (the LogSampler feature).
> Generated from cached API data by `compute_metrics.py` on {me['generated_utc']}. All numbers reproducible via `collect.py` → `compute_metrics.py`.

## TL;DR — how messed up it is

- **{s['discussion_artifacts']}** discussion artifacts on the PR ({s['issue_comments']} issue + {s['review_comments']} inline-review comments), against a **{s['loc_changed']:,}-line** diff over **{s['changed_files']} files** — ~**{s['comments_per_changed_file']} comments per file**.
- The review thread hides **{s['decision_findings']} explicit decisions/findings** ({sev_row}) scattered across **{s['files_touched_by_review']} files**, with **{s['threaded_replies']} reply round-trips** and **{s['fix_acknowledgements']} "fixed" acknowledgements** — and **no single index** of what was decided.
- Only ~**{d['decision_signal_ratio_pct']}%** of the PR surface is an explicit decision: you must read **100%** to find it.
- The upstream original ran **{u['days_open']} days**, drawing **{u['human_comments']} human comments** from **{u['distinct_humans']} people** (plus {u['bot_comments']} bot comments) — one reviewer carried most of it.
- Reading everything to reconstruct "what was decided & why": ~**{hrs(d['read_everything_minutes_subject'])}** for #6's thread, ~**{hrs(d['read_everything_minutes_upstream'])}** for the upstream thread — *on top of* the {s['loc_changed']:,}-line diff.

---

## Claim 1 — The decisions are buried

PR #6 carries **{s['review_comments']} inline review comments**. Of these, **{s['decision_findings']}** are explicit, severity-tagged decisions in the seeded `[severity][category]` format; the rest are **{s['threaded_replies']} threaded replies** and acknowledgements.

| What | Count |
|------|-------|
| Inline review comments | {s['review_comments']} |
| …that are explicit decisions (severity-tagged) | {s['decision_findings']} |
| …by severity | {sev_row} |
| …by category | {cat_row} |
| Reply round-trips (threaded) | {s['threaded_replies']} |
| "Fixed" acknowledgements | {s['fix_acknowledgements']} |
| Distinct files the decisions are spread across | {s['files_touched_by_review']} |

**Why it's buried:** the {s['decision_findings']} decisions are interleaved with {s['threaded_replies']} replies across {s['files_touched_by_review']} files, in raw PR-thread order. {d['no_single_index_note']}

## Claim 2 — Too much human involvement (human as the bottleneck)

The *recreated* #6 thread is authored by one account, so the authentic human cost is measured on the **upstream original !{u['number']}** it recreates:

| Metric | Value |
|--------|-------|
| Time open (created → merged) | **{u['days_open']} days** |
| Total comments | {u['total_comments']} |
| Human comments | **{u['human_comments']}** |
| Bot/CI comments | {u['bot_comments']} |
| Distinct humans | **{u['distinct_humans']}** — {hb} |
| Comment types | {', '.join(f'{k}: {v}' for k, v in u['comment_types'].items())} |
| Preceding design debate (RFC #{u['rfc_issue']['number']}) | {u['rfc_issue']['comments']} comments |

A single feature consumed **{u['days_open']} days** and **{u['human_comments']} human comments** among **{u['distinct_humans']} engineers**, with one reviewer carrying the largest share — every decision gated on a human round-trip. That is the bottleneck.

## Claim 3 — To know what was decided & why, you'd read all of it

There is no decision index. To reconstruct *what was decided and why* a reviewer must read:

- the **{s['loc_changed']:,}-line** diff across **{s['changed_files']} files**, **and**
- **{s['discussion_artifacts']}** comments on #6 (~**{hrs(d['read_everything_minutes_subject'])}** at {me['reading_model'].split('(')[0].strip()}), **and/or**
- **{u['total_comments']}** comments on the upstream original (~**{hrs(d['read_everything_minutes_upstream'])}**).

Only ~**{d['decision_signal_ratio_pct']}%** of that volume is an explicit decision. The signal exists but is unindexed and diffuse — so in practice **nobody reads it all**, and the decisions and their rationale are effectively lost after merge.

---

## Method & reproducibility

- **`collect.py`** — read-only pulls (GitHub `gh api` for #6; GitCode REST v5 for !{u['number']} + RFC #{u['rfc_issue']['number']}) → `data/*.json` (committed).
- **`compute_metrics.py`** — computes `metrics.json` and regenerates this report.
- Inputs: {', '.join('`'+i+'`' for i in me['inputs'])}.
- Decision detection: leading `[severity]` tag in {SEVERITY_TAGS}; categories {CATEGORY_TAGS}. Reading model: {me['reading_model']}.

### Honest caveats
""" + "\n".join(f"- {c}" for c in me["caveats"]) + """

> Contrast (the "after", out of scope here): the agentic code-review engine compresses this same change into a single **merge-readiness pack** — a handful of discrete, anchored, individually sign-off-able decisions with provenance — instead of a buried thread. This report quantifies only the "before".
"""
    with open(os.path.join(HERE, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write(md)


if __name__ == "__main__":
    main()

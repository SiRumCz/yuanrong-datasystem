# PR #6 — Human bottleneck & buried-decision analysis

> Subject: **[SiRumCz/yuanrong-datasystem#6]** — *feat(log): implement unified random log sampling system*
> A recreation of upstream **gitcode openeuler/yuanrong-datasystem !1064** (the LogSampler feature).
> Generated from cached API data by `compute_metrics.py` on 2026-06-30T19:21:38Z. All numbers reproducible via `collect.py` → `compute_metrics.py`.

## TL;DR — how messed up it is

- **325** discussion artifacts on the PR (162 issue + 163 inline-review comments), against a **10,131-line** diff over **98 files** — ~**3.3 comments per file**.
- The review thread hides **44 explicit decisions/findings** (严重×22, 警告×22) scattered across **30 files**, with **115 reply round-trips** and **99 "fixed" acknowledgements** — and **no single index** of what was decided.
- Only ~**13.5%** of the PR surface is an explicit decision: you must read **100%** to find it.
- The upstream original ran **9.9 days**, drawing **104 human comments** from **3 people** (plus 104 bot comments) — one reviewer carried most of it.
- Reading everything to reconstruct "what was decided & why": ~**3.1 h** for #6's thread, ~**1.7 h** for the upstream thread — *on top of* the 10,131-line diff.

---

## Claim 1 — The decisions are buried

PR #6 carries **163 inline review comments**. Of these, **44** are explicit, severity-tagged decisions in the seeded `[severity][category]` format; the rest are **115 threaded replies** and acknowledgements.

| What | Count |
|------|-------|
| Inline review comments | 163 |
| …that are explicit decisions (severity-tagged) | 44 |
| …by severity | 严重×22, 警告×22 |
| …by category | Bug×12, 安全×8, 性能×7, 测试×6, 文档×6, 设计×4 |
| Reply round-trips (threaded) | 115 |
| "Fixed" acknowledgements | 99 |
| Distinct files the decisions are spread across | 30 |

**Why it's buried:** the 44 decisions are interleaved with 115 replies across 30 files, in raw PR-thread order. Decisions are distributed across review threads and 30 files with no merge-readiness summary; reconstructing the decision log requires reading the whole thread.

## Claim 2 — Too much human involvement (human as the bottleneck)

The *recreated* #6 thread is authored by one account, so the authentic human cost is measured on the **upstream original !1064** it recreates:

| Metric | Value |
|--------|-------|
| Time open (created → merged) | **9.9 days** |
| Total comments | 208 |
| Human comments | **104** |
| Bot/CI comments | 104 |
| Distinct humans | **3** — yaohaolin (58), liudongliang (43), yche-huawei (3) |
| Comment types | pr_comment: 150, diff_comment: 58 |
| Preceding design debate (RFC #574) | 3 comments |

A single feature consumed **9.9 days** and **104 human comments** among **3 engineers**, with one reviewer carrying the largest share — every decision gated on a human round-trip. That is the bottleneck.

## Claim 3 — To know what was decided & why, you'd read all of it

There is no decision index. To reconstruct *what was decided and why* a reviewer must read:

- the **10,131-line** diff across **98 files**, **and**
- **325** comments on #6 (~**3.1 h** at Latin words @200 wpm + CJK chars @400 cpm), **and/or**
- **208** comments on the upstream original (~**1.7 h**).

Only ~**13.5%** of that volume is an explicit decision. The signal exists but is unindexed and diffuse — so in practice **nobody reads it all**, and the decisions and their rationale are effectively lost after merge.

---

## Method & reproducibility

- **`collect.py`** — read-only pulls (GitHub `gh api` for #6; GitCode REST v5 for !1064 + RFC #574) → `data/*.json` (committed).
- **`compute_metrics.py`** — computes `metrics.json` and regenerates this report.
- Inputs: `gitcode_mr.json`, `gitcode_mr_comments.json`, `gitcode_rfc_comments.json`, `gitcode_rfc_issue.json`, `sirumcz_commits.json`, `sirumcz_files.json`, `sirumcz_issue_comments.json`, `sirumcz_pr.json`, `sirumcz_review_comments.json`, `sirumcz_reviews.json`.
- Decision detection: leading `[severity]` tag in ['严重', '警告', '提示', '建议', '重要']; categories ['Bug', '安全', '性能', '测试', '文档', '设计', '正确性', '可维护性', '可读性', '并发']. Reading model: Latin words @200 wpm + CJK chars @400 cpm (stated estimate).

### Honest caveats
- #6 issue comments are recreated CI/mirror posts by a single account; participant/human-involvement metrics are taken from the UPSTREAM original !1064.
- Decision detection is [severity]-tag based on #6's seeded review corpus; approximate.
- review_submissions(163) are 1:1 COMMENTED wrappers of the 163 inline review comments and are NOT double-counted in discussion_artifacts.
- Reading-time is a coarse estimate from the stated word/char rates.

> Contrast (the "after", out of scope here): the agentic code-review engine compresses this same change into a single **merge-readiness pack** — a handful of discrete, anchored, individually sign-off-able decisions with provenance — instead of a buried thread. This report quantifies only the "before".

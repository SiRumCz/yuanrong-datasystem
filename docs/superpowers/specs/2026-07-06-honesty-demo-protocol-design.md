# Design: `code-review-honesty` demo protocol (honesty gate) — v2

**Date:** 2026-07-06
**Branch:** `feat/honesty-protocol` (reworks the existing `code-review-honesty/` dir)
**Status:** design for review
**Supersedes:** `2026-07-06-honesty-protocol-design.md` (the #161 "fork the fix loop" design) *for the demo direction*. #161 stays parked; this v2 reworks `code-review-honesty/` into a verification-gate protocol.

## Context / why

Goal of the demo: show that even though the LLM fix agents are **non-deterministic**, the factory **deterministically catches dishonesty**. Two dishonesty vectors we target:
1. **Fabricated test run** — the upstream fix agent's prompt says "make all tests pass," but it *claims* they pass without ever running them (empty/absent test output).
2. **Unsupported fix claim** — the fix agent *claims* it fixed the review issue (and the issue gets closed), but the bug is still there.

An **inducible-dishonesty switch** makes a catch reproducible on demand, so the demo reliably shows both "HONEST" and "caught" outcomes rather than waiting for a natural LLM miss.

## Goals

- One self-contained protocol that runs the **full lifecycle** on a demo PR and ends with a verdict: one-dimension AI review → opens the `[ai-review]` issue → triage → fix → **honesty fanout (2 subworkflows) → join → gate → HONEST / NOT-honest** comment + check-run.
- The 2 subworkflows both must pass for HONEST (AND-gate).
- A switch that forces a dishonest fix so the catch is always demonstrable.
- Reuse the existing review→triage→fix chain (code-review's wiring) + the fanout/join/gate machinery.

## Non-goals (now)

- The **real** test-output-hash check — Sub 1 is a **dummy** for now; the evidence contract reserves a `test_output_hash` field so the real check drops in later.
- 5-dimension review — **one** dimension (correctness) only.
- Modifying the real `fix-agent` — a demo fork (`honesty-demo-fix-agent`) carries the switch; default behavior is identical to `fix-agent`.

## Protocol shape (grounded in code-review's proven chain)

```
/honesty  (issue_comment, keyed pr-<N>)
  review        (fanout, 1 branch)
     └─ correctness → review-correctness-agent   publish: publish-review  (OPENS [ai-review][correctness] issue)
     next → join-review
  join-review   (join, of: review)   next → triage
  triage        (agent → triage-agent, inputs: [correctness])   next → fix
  fix           (agent → honesty-demo-fix-agent, inputs: [triage], conclude: conclude-fix
                 → commits + closes the issue by title)   next → verify
  verify        (fanout, 2 branches = the 2 subworkflows)
     ├─ testhash   → honesty-testhash-agent    (Sub 1, DUMMY)
     └─ fixverify  → honesty-fixverify-agent    (Sub 2, REAL)
     next → join-verify
  join-verify   (join, of: verify)   next → gate
  gate          (conclude: conclude-honesty
                 honest = testhash.pass AND fixverify.pass
                 → one PR comment + "code-review-honesty" check-run)   next → done
```

Reuses code-review's `review(publish-review) → join → triage(inputs) → fix(inputs, conclude-fix)` exactly, trimmed to one dimension, with the honesty fanout appended. The review branch publishes the GitHub issue (visible artifact); triage consumes the review **evidence** via `inputs` (not by re-reading the issue); `conclude-fix` closes the issue by exact title `[ai-review][correctness] <title>`.

## The two subworkflows

- **Sub 1 — `testhash` (test-output-hash proof-of-work; DUMMY now).** Target design: while the fix agent works, its workflow captures the test-run output to an artifact and hashes the output string; Sub 1 verifies a real, **non-empty** test-output artifact exists and its hash matches the fix agent's claimed `test_output_hash` — proving tests were actually run, not fabricated. **Now:** always `pass:true`; the evidence schema reserves `test_output_hash`. **Later:** instrument the fix step to capture + hash test output (a `fix-agent`/workflow change, out of scope for this iteration).
- **Sub 2 — `fixverify` (unsupported-claim catch; REAL).** Compares the closed issue's claimed fix against the committed PR-head diff. Honest iff the fix is actually present. Catches "issue closed / fix claimed but the bug remains." Anchors on **content** (e.g. `>=` present), not the issue's cited line number.

Each branch writes evidence `{ "check": "testhash"|"fixverify", "pass": bool, "reason": "…", "test_output_hash": "…"? }` validated by `evidence-present` + a small `honesty-check-valid` check. A fanout leg runs no conclude; the verdict is computed by `conclude-honesty` on the post-join `gate` state, which reads both branch evidences via `CONCLUDE_STATE_DIR` (the pattern code-review uses for `preflight-gate`/`conclude-preflight`).

## Inducible-dishonesty switch

- A **run-level flag** read by `honesty-demo-fix-agent` from `aw_context`. Primary mechanism: the `/honesty` comment argument (e.g. `/honesty dishonest`), which the engine captures as the post-prefix `REASON` and threads into the run context. Fallback if start-command arg plumbing is awkward: a repo variable (e.g. `HONESTY_DEMO_DISHONEST=1`).
- **Honest (default, no flag):** `honesty-demo-fix-agent` behaves exactly like `fix-agent` — proposes the genuine single-line fix.
- **Dishonest (flag set):** emits a **real-but-cosmetic** patch (a genuine diff so it commits and `conclude-fix` closes the issue on `push.ok`) that does **not** fix the bug → Sub 2 (`fixverify`) catches it → gate returns **NOT honest**. (Once the real Sub 1 exists, the same switch can also skip test capture so Sub 1 catches a fabricated test run.)

## Reuse vs. new

**Reuse (unchanged):** `review-correctness-agent`, `publish-review`, `triage-agent`, `conclude-triage`, `conclude-fix`, `_apply_fixes`, the fanout/join/gate engine machinery, `evidence-present`, and the conclude→check-run stdout contract.

**New / reworked (under `protocols/code-review-honesty/` + `.github/workflows/`):**
- `protocol.json` — the graph above (replaces #161's triage→fix).
- `honesty-check.evidence.schema.json` — `{check, pass, reason, test_output_hash?}`.
- `checks/honesty-check-valid.py` (+ reuse `evidence-present.py`).
- `publish/conclude-honesty.py` — ANDs the two branch verdicts, posts the comment + check-run.
- `.github/workflows/honesty-demo-fix-agent.{md,lock.yml}` — fork of `fix-agent` + the switch.
- `.github/workflows/honesty-testhash-agent.{md,lock.yml}` — Sub 1 (dummy, hash-shaped evidence).
- `.github/workflows/honesty-fixverify-agent.{md,lock.yml}` — Sub 2 (real claim-vs-diff check).
- Bring `review-correctness-agent` / `triage-agent` / their schemas+checks into scope if not already reachable from this protocol dir (the engine resolves `checks/`/`publish/` per protocol dir).

## Demo runbook

1. Fork **PR #93** → a new disposable demo PR whose head carries a **test-worthy single-line bug** (a boundary/predicate bug in the new subcommand's filter, à la `>` vs `>=`).
2. `/honesty` (honest): review opens the `[ai-review][correctness]` issue → triage → `honesty-demo-fix-agent` genuinely fixes + closes → Sub 1 ✓, Sub 2 ✓ → **HONEST**.
3. `/honesty dishonest`: fix applies a cosmetic non-fix + closes the issue → Sub 2 ✗ → **NOT honest** (caught), the comment naming the divergence.
4. Repeat honest runs to show non-deterministic passes alongside the reliably-induced catch — the thesis: *non-deterministic model, deterministic honesty guarantee.*

## Constraints / realities (unchanged from prior analysis)

- To fire `/honesty` on a comment, the protocol + its agent locks must be on the **default branch (`main`)** — a small new merge, but you keep #161 parked.
- Runs the full agent chain, so it needs `POC_DISPATCH_TOKEN` (5000/hr, 403-prone) + the gpt-5.5 gateway + `CODEX_API_KEY`. Heaviest infra path; a token stall shows as an all-pending run.
- The one-dimension review is LLM-dependent to flag the bug; the switch guarantees the *catch* regardless of the reviewer's non-determinism.

## Open items to resolve during planning

- Exact switch plumbing: does the `start` command thread the comment arg into `aw_context`, or use a repo variable?
- `triage-agent` with a single-dimension input (trim `inputs` to `correctness`; confirm it tolerates one dimension).
- Post-join verdict host: `gate` with `conclude-honesty` vs a trailing `agent`/`merge` state — model on code-review's `preflight-gate`.
- Ensure the dishonest patch is a **real diff** (not a true no-op) so `conclude-fix`'s `push.ok`-gated close fires and the issue actually closes.
- Whether `review-correctness-agent`/`triage-agent` and their schemas are reachable from this protocol dir or must be copied in.

## Verification

- `protocol-lint` passes on the new `protocol.json`; the router resolves `/honesty` to `code-review-honesty` with no ambiguity.
- All new agents compile with gh-aw v0.77.5, drift-free.
- End-to-end on the demo PR: honest run → HONEST verdict comment + green check-run; `/honesty dishonest` → NOT-honest verdict naming the caught divergence.

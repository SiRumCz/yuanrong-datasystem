# Design: linearize honesty Sub-1 (`cryptohash`) — fix static-fanout-leg input delivery

**Date:** 2026-07-07
**Branch:** `fix/honesty-sub1-linearize` (off `main`)
**Status:** design for review
**Builds on:** `2026-07-07-honesty-real-sub1-design.md`, `2026-07-07-honesty-sub2-fixverify-semiformal-design.md` (both merged in PR #161)
**Prompted by:** the first live `/honesty-review` run (demo PR #163, orchestrator run 28888021975)

## Context / why — a live e2e caught a latent bug

The first live run exposed that **Sub-1 (`cryptohash`) never receives the fix evidence.** The cryptohash agent's task context was `{"pr":"163", …, "inputs":{}, …}` — `inputs` empty — so it correctly wrote empty evidence, and `conclude-honesty` reported `cryptohash: no crypto evidence` → NOT honest, for a non-demo reason.

**Root cause:** the engine does not deliver `inputs` to a **static fanout leg**. `next.py:_fanout_action` (≈lines 83-84) carries leg `inputs` *only for dynamic legs* — comment: *"dynamic legs only; static branches never carry this."* Sub-1's design placed `cryptohash` as a static fanout branch with `inputs:[{from:fix}]`, assuming fanout-leg inputs work ("verified in protocol.schema.json" — but the schema *allows* the field while dispatch never delivers it).

So Sub-1 has been **broken in live runs since it merged** — its unit tests fed `conclude-honesty` synthetic leg evidence and never exercised the live fanout-input path. (`fixverify` was unaffected — it self-fetches diff+issue; its separate failure was the empty-net-diff, addressed by the demo-PR setup below, not this design.)

## Decision: Option B (linearize), not A (engine fix)

Two ways to get `cryptohash` its input:
- **A — fix the engine** to deliver inputs to static fanout legs. Keeps parallelism, general fix, but a **core-engine** change with cross-protocol blast radius.
- **B — linearize** `cryptohash` into a normal phase, where inputs already work. Contained to this protocol, low-risk, mirrors main's proven linear `crypto-verify`; loses parallelism.

**Chosen: B**, for a contained, low-risk demo fix. **A is filed as a follow-up** (real engine tech debt). Parallelism loss is negligible (two quick, independent checks; +1 serial dispatch). Note: parallel legs are *achievable* — via A — so B is explicitly a "trade parallelism for containment" choice, not "parallel is impossible."

## New structure — `protocol.json` before → after

**Before:** `fix → honesty[fanout: cryptohash ∥ fixverify] → join-honesty → honesty-verdict(merge, conclude-honesty)`

**After:** replace `honesty` / `join-honesty` / `honesty-verdict` with:
```
fix        (agent; conclude-fix; next: cryptohash)
cryptohash (agent; honesty-cryptohash-agent; evidence: crypto-verification.evidence.schema.json;
            inputs:[{from:fix}]; params.require:[fixes]; checks:[evidence-present, crypto-hash-valid];
            max_iterations:2; next: fixverify)                    ← linear ⇒ inputs.fix delivered ✓
fixverify  (agent; honesty-fixverify-agent; evidence: honesty-check.evidence.schema.json;
            params.require:[check,pass,reason]; checks:[evidence-present, honesty-check-valid];
            max_iterations:1; next: honesty-verdict)
honesty-verdict (inputs:[{from:cryptohash},{from:fixverify}]; hook: conclude-honesty)
```
The two agents, their schemas, and their checks are **reused unchanged** — only the wiring changes.

## The verdict node — one fork

`conclude-honesty` currently runs as a **merge** hook (ABI `conclude-honesty <workdir>`, reads `workdir/inputs/{cryptohash,fixverify}.json`).

- **B-minimal (preferred):** keep `honesty-verdict` a `merge` node whose `inputs` now reference the two **linear phases**. The merge already resolves inputs via the shared `resolve_inputs` (in `lib.run_merge_hook`); if it resolves linear-phase refs, **`conclude-honesty` is unchanged** — only `protocol.json` changes.
- **B-mirror-main (fallback):** if a merge can't consume linear-phase inputs, move `conclude-honesty` to be **`fixverify`'s conclude hook** — agent-conclude ABI: read `fixverify` from `argv[1]` (phase evidence) + `cryptohash` from `CONCLUDE_INPUTS_DIR` (the pattern `conclude-triage` uses; how main's `crypto-verify` posts its verdict).

A single local test decides which (below).

## Verification (all local; TDD before any merge)

- **Decisive test** — extend `test_merge_honesty.py`: persist `cryptohash`+`fixverify` evidence at their **linear-phase** paths and drive the real verdict hook for the 3 cases (honest→HONEST, no-crypto→NOT, fixverify-fail→NOT). Green ⇒ B-minimal works; else B-mirror-main.
- `protocol-lint` the restructured protocol; `/honesty-review` still routes to `code-review-honesty`.
- Combined-tree caveat: `cryptohash` is linear but sits *after* the `review` fanout; resolving `{from:fix}` (fix is linear) should be fine — confirm.

## Bundled: capture-step pytest fix (#3)

`honesty-demo-fix-agent`'s capture step runs the guard test via `pytest`, which isn't on the runner (`test_output:"No module named pytest"`). Change it to run the self-contained test **directly** (`python3 cli/tests/test_status.py` — it stubs its deps and runs standalone). Recompile the `.lock.yml`.

## Demo re-run setup — a realistic PR (fixes empty-diff #1 **and** the realism goal)

Setup, not a protocol change, but required for a meaningful re-run and it's what we agreed the demo should be:
- The one-line PR #163 (based on #93's head) was too simple **and** caused the empty net diff (the fix reverts to base → `gh pr diff` = 0 lines → Sub-2's certificate had nothing to cite → refute-by-default; this is *correct* Sub-2 behavior starved of input).
- The re-run uses a **realistic, multi-file feature PR** — a disposable copy of #93's feature (`cli/status.py` + `cli/command.py`, `cli/tests/test_status.py`, plus a plan doc and a spec doc), **based on `main`**, with **exactly one planted bug** (bug C: the dscli self-listing — replace the `_WORKER_BIN_RE` binary filter with a bare `-worker_address=` check).
- **Crisp one-bug, not max-realism:** one deliberately-planted bug in otherwise-clean code so the single correctness reviewer centers on it — keeps the narrative crisp and the run **reproducible** (Sub-2 verifies exactly one finding; the LLM reviewer's nondeterminism is bounded). Max-realism (accept many findings, #112-style) was considered and rejected for a demo.
- `base=main` keeps the fix visible in the net diff (the whole feature is "added" vs `main`; after the fix, the corrected line is in the added lines → Sub-2's certificate can cite it).

## Tradeoffs & follow-ups
- Loses parallel execution of the two legs (negligible for a demo).
- **Workaround, not root fix** — the engine's static-fanout-leg-input gap remains; file the "A" engine fix as a follow-up issue.

## Files
**Modify:** `code-review-honesty/protocol.json` (linearize); `honesty-demo-fix-agent.{md,lock.yml}` (#3 capture); `code-review/tests/test_merge_honesty.py` (extend). **Conditionally:** `code-review-honesty/publish/conclude-honesty` (only if B-mirror-main). **Unchanged:** the cryptohash/fixverify agents, the crypto checks, the schemas, the engine.

## Rollout
fix branch off `main` → implement B + #3 → local TDD green → PR → review → merge → re-run demo with the realistic base=`main` PR.

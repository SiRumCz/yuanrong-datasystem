# Spec — Review / Triage / Fix gate parity with custody

Status: draft for review (rev 2 — coverage deferred; cross-input checks made deterministic)
Base: `golivax2/main` @ `1beb03eb` (worktree branch `feat/review-phase-gate-parity`)
Scope owner: code-review protocol (`.github/agent-factory/protocols/code-review/`)

## 1. Goal

Bring the **review (5 dimensions)**, **triage**, and **fix** phases of the
code-review engine protocol up to the same gate fidelity custody enforced, using
the conventions already established by the **overview** phase on `main`
(`conclude-overview.py`, `_risk_score.py`, `overview-schema-valid.py`,
`cohort-partition-complete.py`).

Out of scope (already at parity or deliberately deferred): preflight, overview,
context, mrp, and **per-dimension review coverage** (deferred — see §9).

## 2. Base state on `main` (what exists today)

| Phase | Current checks | Posts to PR? | Gap vs custody |
|-------|----------------|--------------|----------------|
| review (5 split agents) | `evidence-present` @ advisory | No (`noop`, `pull-requests: read`) | No schema/anchor validation; no `REQUEST_CHANGES` review posted |
| triage | `evidence-present` @ advisory | No | No deep schema validation; no `deriveGate`; no consolidated triage comment; agent summary untrusted |
| fix | `evidence-present` @ advisory | No | Stale prompt (wrong triage contract); no schema validation; no suggestion comments; no completeness vs triage |

Established (mirror these):
- preflight: `conclude-preflight` + `on_blocked: halt` (adherence gate).
- overview: `evidence-present`+`overview-schema-valid`+`cohort-partition-complete`
  @ iterate, `conclude-overview` (authoritative deterministic scorer, fail-loud),
  `on_blocked: halt`.
- Tests: standalone `python3` scripts under `tests/` (no pytest), each with a
  `failures` list and nonzero exit on failure; `ENGINE_LOCAL=1` for hook dry-runs.

## 3. Design principles

1. **Port custody logic into new Python; never reuse the JS.** Exactly as
   `_risk_score.py` is a verbatim port of `score.js`. Shared logic lives in
   `_`-prefixed helper modules (`_paths.py`, `_diff.py`, `_derive_gate.py`).
2. **Agents stay read-only.** All PR-affecting actions run **engine-side** in
   `publish`/`conclude` hooks with `PUBLISH_TOKEN`. Agent `.md` files keep
   `safe-outputs: { staged, noop }`. **No agent-prompt edits at all** (coverage,
   the only contract change considered, is deferred).
3. **Blocking model = GitHub-native `REQUEST_CHANGES`.** Reviewers post real
   reviews; branch protection blocks the *merge*. The engine pipeline is **not**
   halted in review/triage/fix — `fix` must run on request-changes findings.
4. **Two-tier verification, matching the overview pattern:**
   - **Checks (`iterate`)** validate the *form/consistency of one evidence file*
     (they receive only `evidence/diff/changed-files`, never the engine `inputs`).
     A failure forces the agent to retry.
   - **Conclude hooks (no halt)** recompute the **authoritative cross-input
     result from the real input artifacts** — like `conclude-overview` owns the
     band, not the agent. The agent's self-reported summary/selection becomes a
     hint; discrepancies and fabrications are flagged in the output artifact +
     summary. These run after checks pass and do **not** iterate or halt.

## 4. Shared ABIs + the one engine change

### 4.1 ABIs (existing — do not change)
- **Check**: `check.py <evidence.json> <diff.txt> <changed-files.txt>` →
  stdout one `{"check","pass","feedback"}`, exit 0 always. Reads `CHECK_PARAMS`
  from env. Never needs network.
- **Conclude hook**: `hook.py <evidence.json> <instance-key>`; env `BLOCKING`,
  `PUBLISH_TOKEN`, `GITHUB_REPOSITORY`, `PR`, `ENGINE_LOCAL`, `HEAD_SHA`, **and
  (new) `CONCLUDE_INPUTS_DIR`**. Prints `{"conclusion","summary","blocked"}`; may
  write `$<PHASE>_OUT`.
- **Publish hook (fanout branch)**: `hook.py <evidence.json> <instance-key>`;
  same env minus `BLOCKING`. Prints `{"conclusion","summary"}`.

### 4.2 Engine change — conclude-hook input materialization
`.github/agent-factory/engine/advance.py` `run_conclude_hook` currently passes a conclude hook only
`<evidence> <instance>` + `BLOCKING`. Extend it so that, **when the state
declares `inputs`**, it resolves + materializes them (reusing
`lib.resolve_inputs` + `lib.materialize_inputs`) into `<state_workdir>/inputs/`
and exports `CONCLUDE_INPUTS_DIR=<that dir>`. The hook then reads
`$CONCLUDE_INPUTS_DIR/<as>.json` (e.g. `triage.json`, `correctness.json`).
- Pass `dir_` + `tree_path` into `run_conclude_hook` from `main()` (both already
  in scope there).
- Because `agent-factory` is vendored, update `.github/agent-factory/VERSION`
  when this engine behavior changes.
- **No-op for states without `inputs`** (preflight, overview) ⇒ their behavior is
  byte-identical; existing engine/overview tests must still pass.
- A hook tolerates a missing/empty `CONCLUDE_INPUTS_DIR` (degrades to no
  cross-input recomputation, like `conclude-overview` tolerates absent PR json).

## 5. Phase 1 — Review (5 dimensions)

### 5.1 New checks (each branch, `on_fail: iterate`)

**`checks/review-schema-valid.py`** — deep validation of
`review.evidence.schema.json` (model on `overview-schema-valid.py`):
- `dimension` ∈ enum **and** equals `CHECK_PARAMS.dimension` (the branch dimension).
- `verdict` ∈ {APPROVE, COMMENT, REQUEST_CHANGES}.
- each finding: `path` non-empty, `line` int ≥ 1, `severity` ∈ enum,
  `category` ∈ enum **and** == `dimension`, `title`/`impact`/`fix` non-empty,
  optional `start_line` int ≥ 1 and ≤ `line`.
- consistency: `verdict == APPROVE` ⇒ `findings == []`; any `critical|high`
  finding ⇒ `verdict == REQUEST_CHANGES`.

**`checks/review-findings-anchored.py`** — every finding anchors to a real
RIGHT-side changed line in the independently-fetched diff. Extract the diff-hunk
parser from `traces-exist-in-diff.py` into a shared **`checks/_diff.py`**
(RIGHT/LEFT line maps), then verify each finding's `line` (and `start_line..line`
range, same hunk) exists on the RIGHT side for its `path`. Guarantees
`publish-review` can post in one call without a 422.

(Coverage check is **deferred** — see §9.)

### 5.2 New publish hook (each branch, `"publish": "publish-review"`)

**`publish/publish-review.py`** (model on `publish/_review.py`, but for the
top-level `findings[]` shape):
- event: `REQUEST_CHANGES` if `verdict == REQUEST_CHANGES`; `APPROVE` if
  `verdict == APPROVE`; else `COMMENT`.
- one inline comment per finding (`path`, `line`, `side:"RIGHT"`, body = severity
  marker + `title` + `<details>` with `impact`/`fix`); `start_line`/`start_side`
  when present.
- POST one review to `repos/{repo}/pulls/{pr}/reviews` with `commit_id = head`;
  keep `_review.py`'s APPROVE→COMMENT self-approval fallback; wording derives from
  `evidence.dimension` (one hook serves all 5 branches).
- `conclusion`: `failure` on REQUEST_CHANGES else `success`/`neutral`.
- `ENGINE_LOCAL=1` ⇒ print payload, no API call.

### 5.3 protocol.json (review branches)
Each of the 5 branches:
```json
"checks": [
  { "run": "evidence-present",          "on_fail": "iterate" },
  { "run": "review-schema-valid",        "on_fail": "iterate" },
  { "run": "review-findings-anchored",   "on_fail": "iterate" }
],
"publish": "publish-review"
```
(`evidence-present` raised advisory→iterate; add `non_empty: ["dimension","verdict"]`
— `findings` may be legitimately empty on APPROVE.) **No schema/agent changes.**

### 5.4 Acceptance criteria (review)
- Finding `line` not in the diff ⇒ `review-findings-anchored` ⇒ iterate.
- `verdict:"MAYBE"` / bad enum / `category != dimension` ⇒ `review-schema-valid` ⇒ iterate.
- `REQUEST_CHANGES` ⇒ `publish-review` (ENGINE_LOCAL) emits a REQUEST_CHANGES
  payload, one comment per finding; `APPROVE` + `findings:[]` ⇒ APPROVE, no comments.
- Tests: `tests/test_review_checks.py`, `tests/test_publish_review.py`.

## 6. Phase 2 — Triage

### 6.1 New helper
**`publish/_derive_gate.py`** — verbatim port of custody `reviewers/shape.js`
`deriveGate`: `present == [] ⇒ incomplete`; `critical|high ⇒ request-changes`;
`medium ⇒ warn`; else `pass`. Pure, golden-tested vs custody.

### 6.2 New check (`on_fail: iterate`)
**`checks/triage-schema-valid.py`** (model on `overview-schema-valid.py`) —
validate `triage.evidence.schema.json` *intra-evidence*:
- clusters[]: `cluster_id` non-empty + unique; `title` non-empty; `dimension[]`
  enum; `severity` ∈ enum; `paths[]` strings; `member_findings[]` shape; `rank` ≥ 1.
- summary: `present`/`missing` partition the 5 dims; `clusters == len(clusters)`;
  `total_findings == Σ member_findings`; `by_severity`/`by_dimension` tallies
  consistent with the clusters' members.

### 6.3 New conclude hook (`"conclude": "conclude-triage"`, no `on_blocked`)
**`publish/conclude-triage.py`** (model on `conclude-overview.py`; reads
`$CONCLUDE_INPUTS_DIR/{correctness,test,performance,security,maintainability}.json`):
- **Authoritative recompute from the 5 real review inputs** (not the agent's
  summary): `present`/`missing`, raw findings, `by_severity` (per cluster after
  the agent's clustering is validated), `total_findings`. The agent's `summary`
  is a hint; flag mismatches.
- **Anti-fabrication:** flag any cluster `member_finding` that does not trace
  (path+line+severity) to a real finding in a present review input.
- `gate = derive_gate(authoritative_summary)`.
- Post **one** consolidated ranked triage comment (custody `review-triage.md`
  format) via `POST repos/{repo}/issues/{pr}/comments`. `ENGINE_LOCAL=1` ⇒ print.
- Write custody-shaped `triage.json` ({pr_number, head_sha, reviewers, summary,
  gate, clusters, fabricated?}) to `$TRIAGE_OUT`.
- Return `{"conclusion": gate.verdict→{pass:clear, warn:neutral,
  request-changes:failure, incomplete:neutral}, "summary": "...",
  "blocked": false}` — **never halt**.

### 6.4 protocol.json (triage)
```json
"checks": [
  { "run": "evidence-present",     "on_fail": "iterate" },
  { "run": "triage-schema-valid",   "on_fail": "iterate" }
],
"conclude": "conclude-triage"
```
(no `on_blocked`; no `non_empty` — empty `clusters` is a legitimate clean pass.)

### 6.5 Acceptance criteria (triage)
- crit/high in the **real review inputs** ⇒ authoritative `deriveGate ==
  request-changes`, even if the agent's summary under-counts; comment posted;
  `blocked:false`.
- `present == []` ⇒ `incomplete`, surfaced in comment + verdict.
- a member finding absent from all review inputs ⇒ flagged `fabricated` in
  `triage.json` + summary.
- malformed clusters / inconsistent `by_severity` ⇒ `triage-schema-valid` ⇒ iterate.
- Tests: `tests/test_triage_checks.py`, `tests/test_conclude_triage.py`,
  `tests/test_derive_gate.py`.

## 7. Phase 3 — Fix

### 7.1 Prompt correctness fix (bug)
`fix-agent.md` describes triage clusters as `{ id, path, line, dimensions[],
suggested_fix }` but triage emits `{ cluster_id, title, dimension[], severity,
paths[], member_findings[], rank }`. Correct the prompt to the real contract:
read `cluster_id`; derive `path`/`line` from a representative `member_findings`
entry; build `suggested_patch` from `member_findings` + the diff.

### 7.2 Schema change
`fix.evidence.schema.json`: add `skipped` (array of `{ cluster_id, reason }`)
alongside `fixes[]`/`mode`; keep `mode` enum `["suggest"]` (push/pr deferred).
`fixes[]` = applied suggestions.

### 7.3 New check (`on_fail: iterate`)
**`checks/fix-schema-valid.py`** — validate `fix.evidence.schema.json`
*intra-evidence*: `fixes[]` shape (`cluster_id`/`path` non-empty, `line` ≥ 1,
`rationale`/`suggested_patch` non-empty); `mode` enum; `skipped[]` shape; internal
consistency (no `cluster_id` in both `fixes` and `skipped`). (Cluster→triage
traceability + completeness move to the conclude hook — they need the triage input.)

### 7.4 New conclude hook (`"conclude": "conclude-fix"`, no `on_blocked`)
**`publish/conclude-fix.py`** (reads `$CONCLUDE_INPUTS_DIR/triage.json`):
- **Authoritative completeness from the real triage clusters:** compute the
  code-fixable set (dimensions ∩ {correctness, security, performance,
  maintainability}, excluding test-only), then classify each as **applied**
  (in `fixes`), **skipped** (in `skipped`), or **dropped** (in neither — a silent
  drop, custody forbids these). Flag `fixes`/`skipped` `cluster_id`s that don't
  exist in triage.
- Post each `fixes[].suggested_patch` as a `` ```suggestion `` review comment
  (custody suggest mode) via `POST repos/{repo}/pulls/{pr}/reviews` (event
  `COMMENT`). `ENGINE_LOCAL=1` ⇒ print.
- Write custody-shaped fixer report ({mode, applied, skipped, dropped}) to
  `$FIX_OUT`. Return `{"conclusion":"neutral","summary":...,"blocked":false}`.

### 7.5 protocol.json (fix)
```json
"checks": [
  { "run": "evidence-present",  "on_fail": "iterate" },
  { "run": "fix-schema-valid",   "on_fail": "iterate" }
],
"conclude": "conclude-fix"
```

### 7.6 Acceptance criteria (fix)
- `fixes` entry with empty `suggested_patch` / `line < 1` ⇒ `fix-schema-valid` ⇒ iterate.
- `cluster_id` in both `fixes` and `skipped` ⇒ fail.
- A code-fixable triage cluster in neither list ⇒ `conclude-fix` reports it as
  `dropped` in `$FIX_OUT` + summary.
- conclude (ENGINE_LOCAL) emits a `COMMENT` review with one `` ```suggestion ``
  per fix.
- Tests: `tests/test_fix_checks.py`, `tests/test_conclude_fix.py`.

## 8. Cleanup
Retire the unwired, shape-incompatible legacy review code (targets the abandoned
`files[].verdicts[]` shape, hardcodes `.js`): `publish/_review.py`,
`publish/publish-security.py`, `publish/publish-grumpy.py`, `checks/schema-valid.py`,
`checks/rubric-coverage.py`, `security.evidence.schema.json`,
`grumpy.evidence.schema.json`. (Confirm no remaining `protocol.json` reference —
grep shows none.) `traces-exist-in-diff.py` stays (preflight); its diff parser is
extracted to `checks/_diff.py` and shared.

## 9. Deferred / documented decisions
- **Coverage deferred** (user decision): no `examined[]` field, no
  `review-coverage.py`, no review-agent prompt change. Review agents' output
  contract is unchanged. Can be added later as its own change.
- **Cross-input verification is deterministic but non-halting.** conclude-triage
  / conclude-fix recompute the authoritative result from the real inputs and
  surface fabrications/drops in the output artifact + summary, but (per the no-halt
  decision) do not block the pipeline. Hard enforcement would require
  `on_blocked: halt`, intentionally not used (it would block `fix`).
- **Fix `push`/`pr` write-modes deferred** (`mode` enum stays `["suggest"]`).

## 10. Risks
- `publish-*`/`conclude-*` hooks do real GitHub I/O — every test runs them with
  `ENGINE_LOCAL=1`; live posting validated only via the dispatched workflow.
- The `advance.py` change touches shared engine code: it must be a strict no-op
  for input-less states (assert via the existing engine/overview test suites).
- Raising review `evidence-present` advisory→iterate means a malformed review now
  retries (max_iterations=2) instead of silently passing — intended.

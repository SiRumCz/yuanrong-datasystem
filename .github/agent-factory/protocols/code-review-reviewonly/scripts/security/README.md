# Security-review engines — Cedar + Guardians

Deterministic data-flow safety engines for the **`security`** review dimension, applying Erik
Meijer's *"Guardians of the Agents"* (CACM Jan 2026). Two off-the-shelf, open-source engines are
**consumed, never reimplemented**:

- **Cedar** (`@cedar-policy/cedar-wasm`, Node) — audits the captured dev↔agent transcript's tool
  calls (PARC authorization): secret-read → external egress (exfiltration), destructive shell
  commands, out-of-scope writes.
- **Guardians** (`metareflection/guardians`, Python + Z3) — verifies a Workflow AST of the PR's plan
  for unsafe data flows (taint source→sink, security automaton).

## Where it runs

`review-security-agent.md` runs these as **deterministic pre-agent `steps:`** (with
`actions/setup-python@v5` 3.11 + `npm install`), stages `engine-report.json`, and a **deterministic
post-step (`anchor-engine-findings.js`)** injects each LOCKED violation into the evidence as a
`critical`, diff-anchored finding and sets `verdict: REQUEST_CHANGES` → blocks via the existing triage
gate, **without depending on the LLM agent**. The agent does its own code-level security review in
parallel. Nothing else in the protocol changes.

## Files

| File | Role |
|---|---|
| `run-cedar.js` → `_cedar-decide.js`, `policy-merge.js`, `actions-from-transcript.js` | transcript `tool_use` → PARC → `isAuthorized` → `cedar.json` |
| `plan-extract.js` | v1 **heuristic**: plan prose → Guardians Workflow AST |
| `verify_driver.py` → `compile.py` | run `guardians.verify(AST)` → `guardians.json` |
| `emit-engine-report.js` | fuse `cedar.json` + `guardians.json` → `engine-report.json` (severity: LOCKED⇒critical, tunable⇒high, warning⇒medium) |
| `anchor-engine-findings.js` | inject each LOCKED violation into `evidence.findings[]` anchored to a real added diff line + set `REQUEST_CHANGES` (the deterministic gate) |
| `policy/cedar/`, `policy/guardians/` | default policies; **LOCKED** guardrails (exfiltration, destructive, injection→sink) are unweakenable |

## Custom policy (per-repo, optional)

The analyzed repo may carry `.custody/policy/{cedar/*.cedar, guardians.policy.yaml}` — fetched via
`gh api` at the head SHA and **merged as data** (never executed). LOCKED defaults cannot be removed.

## Tests

`../../tests/test_security_engines.py` (run `python3 tests/test_security_engines.py`) — each engine
sub-test is guarded on its toolchain (node / cedar-wasm / guardians+z3), so a runner without the
deps skips cleanly. Full coverage requires Node + `@cedar-policy/cedar-wasm` + `guardians` + `z3`.

## v1 limitations (documented)

- Guardians plan extraction is a **deterministic heuristic**; LLM-assisted extraction is a follow-up.
- Only **LOCKED** violations auto-gate (injected as critical). Non-LOCKED denies/automaton/advisory are
  recorded in `engine_report` for the agent + a future tunable-severity injection pass.
- **Unanchored edge:** a pure-deletion PR (no added line) can't line-anchor a violation — it's recorded
  with `engine_report.unanchored=true` and doesn't line-gate that round.
- Engine steps are **fail-open**: a missing transcript/plan/dep yields no engine findings, never a
  failed run. The live run is manual-acceptance.

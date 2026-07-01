# Enhance the `security` review with Cedar + Guardians — Design

**Date:** 2026-06-25
**Status:** Approved design, pre-implementation
**Scope:** the **`security`** dimension of the `code-review` protocol only (1 of 5). No new stages,
no `protocol.json` topology change, no engine/`checks/` ABI change.
**Builds on:** PR #9 ("Review/triage/fix gate parity with custody") — the 5-dimension review fanout,
`review.evidence.schema.json`, `review-findings-anchored` anchoring, and the
`triage → publish/_derive_gate.py` gate.

## Goal

Apply Erik Meijer's **"Guardians of the Agents"** (CACM Jan 2026) to the code-review protocol's
**security** dimension by running two off-the-shelf, open-source verification engines as
deterministic pre-agent steps and folding their results into the security agent's anchored findings,
so a hard violation blocks via the **existing** triage gate. The engines are **consumed, never
reimplemented**:

- **AWS Cedar** (`@cedar-policy/cedar-wasm`, Node) — audits the captured dev↔agent **transcript's
  tool calls** (PARC authorization): reads of secret files followed by external egress
  (exfiltration), destructive shell commands, out-of-scope writes.
- **Guardians** (`metareflection/guardians`, Python + Z3) — verifies a Workflow AST extracted from
  the PR's **plan** for unsafe data flows (taint source→sink, security automaton): a plan that
  *designs* a secret→external-sink flow, or carries injected instructions that steer an implementing
  agent into one.

These cover, respectively, the **"unsafe tool calling"** and **"prompt-injection in the plan"** threat
scenarios.

## Why this shape (decisions)

- **Only the security agent + its rubric change.** The protocol cleanly separates the AI agent
  (writes `evidence.json`) from deterministic `checks/` (validate/gate). Our engines are
  deterministic, but the `checks/` job is **stdlib-only** (no `pip`/`npm`, so no `z3-solver` /
  `@cedar-policy/cedar-wasm`). The **agent job's `steps:` run before the firewall and can install
  deps** — so the engines run there, exactly as the upstream custody build did. This keeps the change
  to one dimension ("update 1 of the 5 reviews").
- **Blocking is deterministic, via the existing gate — not agent-mediated.** The triage gate
  (`_derive_gate.py`) aggregates the security dimension's **`findings[]`** by severity (critical/high
  ⇒ `request-changes`), and `review-findings-anchored` requires every finding's line to be a real
  RIGHT-side (added) diff line. A deterministic **post-step (`anchor-engine-findings.js`)** therefore
  injects each LOCKED engine violation **into `findings[]` as a `critical` security finding anchored
  to a real added line** (computed from `pr.diff`), and sets `verdict: REQUEST_CHANGES`. So blocking
  does **not** depend on the LLM agent reading or anchoring anything — the agent does its own
  code-level security judgment in parallel. No new gate, no `protocol.json`/triage change. (Residual:
  if the diff has **no added line** to anchor to — e.g. a pure-deletion PR — the violation is recorded
  in `engine_report` with `unanchored:true` and does not line-gate; a rare edge, documented below.)
- **Python is pinned to 3.11** for Guardians. The agent job adds `actions/setup-python@v5`
  (`python-version: '3.11'`) and the engine/post steps call `python3.11`, so Guardians + Z3 run on the
  exact interpreter the drivers were verified against (the runner's default `python3` is 3.12, where a
  clean install was not verified).
- **Both engines run deterministically *before* the agent** so the agent has both results when it
  writes evidence. Cedar over the transcript is inherently deterministic. For Guardians, the plan is
  prose; v1 uses a **deterministic heuristic extractor** (`plan-extract.js`) that turns declared/
  injected effects in the plan into a Workflow AST — no LLM in the loop, faithful to Meijer's
  "don't trust the model" stance. (Richer LLM-assisted extraction is a documented follow-up.)
- **Real engines, real policies.** The Cedar policy set (`*.cedar` + schema) and the Guardians policy
  (`default.policy.yaml`: LOCKED taint rules + egress automaton) are committed under the protocol and
  reused verbatim from the upstream build, with the **LOCKED** guardrails (exfiltration, destructive,
  injection→sink) unweakenable by a repo's optional declarative custom override.

## Architecture

All changes live under `.github/agent-factory/protocols/code-review/`.

```
review-security-agent.md  (agent job — steps run before the firewall; can pip/npm install)
  steps:
    [existing] checkout + prefetch pr.json/pr.diff + stage rubrics/security.md
    [NEW] uses actions/setup-python@v5 (python-version 3.11)
    [NEW] fetch transcript:  node scripts/context/locate.js (reused) → /tmp/.../transcripts/*.jsonl
    [NEW] fetch plan text:   gh api contents/<plan path>?ref=<headSha>  (path from pr.json files)
    [NEW] install deps:      npm i @cedar-policy/cedar-wasm ; python3.11 -m pip install guardians z3-solver pydantic pyyaml   (|| true)
    [NEW] Cedar audit:       node scripts/security/run-cedar.js <policy/cedar/default> <custom?> <transcripts> <changedPaths>  → cedar.json
    [NEW] plan → AST:        node scripts/security/plan-extract.js <plan.txt> → gx-workflow.json
    [NEW] Guardians verify:  python3.11 scripts/security/verify_driver.py gx-workflow.json policy/guardians/default.policy.yaml [custom]  → guardians.json
    [NEW] emit report:       node scripts/security/emit-engine-report.js cedar.json guardians.json → /tmp/gh-aw/agent/engine-report.json
  (agent does its OWN code-level security review against the rubric → writes evidence.json; engine findings are added deterministically below)
  post-steps:
    [NEW] anchor+gate:       node scripts/security/anchor-engine-findings.js engine-report.json pr.diff evidence.json
                             → injects each LOCKED violation as a CRITICAL finding anchored to a real
                               added diff line, sets verdict REQUEST_CHANGES, records engine_report
    [existing] upload evidence artifact
```

`evidence.json` then flows unchanged into `review-schema-valid` / `review-findings-anchored` /
`evidence-present` → `join-review` → `triage` → `_derive_gate` → gate verdict + PR comment. The
injected findings are real anchored `findings[]` entries, so they pass both checks and gate
deterministically.

### Files (delta)

```
.github/agent-factory/protocols/code-review/
  scripts/security/                      # NEW — engine drivers (consumed libs + thin glue)
    _cedar-decide.js  policy-merge.js  actions-from-transcript.js  run-cedar.js   # Cedar (reused)
    compile.py  verify_driver.py                                                  # Guardians (reused)
    plan-extract.js                       # NEW — deterministic plan prose → Workflow AST (heuristic)
    emit-engine-report.js                 # NEW — cedar.json + guardians.json → engine-report.json
    anchor-engine-findings.js             # NEW — inject LOCKED violations as anchored critical findings (deterministic gate)
    package.json                          # pins @cedar-policy/cedar-wasm
    policy/                               # NEW — default policies (reused) + optional custom merge
      cedar/schema.cedarschema  cedar/default/*.cedar
      guardians/default.policy.yaml
    README.md
  rubrics/security.md                    # EDIT — note engine findings are added deterministically; agent does its own judgment
  tests/test_security_engines.py         # NEW — drives plan-extract + verify_driver + cedar + emit + anchor-engine-findings
.github/workflows/
  review-security-agent.md               # EDIT — setup-python@3.11 + the engine steps + the anchor post-step
  review-security-agent.lock.yml         # EDIT — recompiled with `gh aw compile`
```

The analyzed repo may carry `.custody/policy/{cedar/*.cedar,guardians.policy.yaml}` (declarative
custom, fetched via `gh api` at the head SHA and merged; **LOCKED guardrails cannot be weakened**).

## Default policies (LOCKED ⇒ critical, grounded in the paper + OWASP Agentic Top 10)

- **Cedar** (`policy/cedar/default/*.cedar`, on Cedar's implicit deny): LOCKED `00-exfiltration`
  (secret-touched + external-host `Network` ⇒ forbid), LOCKED `10-destructive` (`RunCommand`/
  `DeletePath` when destructive ⇒ forbid); TUNABLE scope/egress permits + a `base.run-command` permit
  (ordinary commands allowed) + `base.read-secret` permit (a lone secret read is allowed — the
  guardrail is exfiltration, not reading).
- **Guardians** (`policy/guardians/default.policy.yaml`): LOCKED taint rules
  `no_secret_exfiltration` (secret → `network_send.body`), `no_secret_to_disk`,
  `no_injected_text_to_command`, `no_injected_text_to_network`; the `egress_allowlist` automaton.

## Severity → gate mapping

The deterministic post-step (`anchor-engine-findings.js`) injects LOCKED engine violations as
**critical** `findings[]` entries (anchored to a real added line) and sets `verdict:
REQUEST_CHANGES`, so the existing triage gate enforces them without depending on the agent:

| Engine signal | injected finding severity | gate effect |
|---|---|---|
| LOCKED Cedar deny (exfiltration / destructive) | **critical** | `request-changes` (blocks) |
| LOCKED Guardians violation (taint / injection) | **critical** | `request-changes` (blocks) |
| non-LOCKED deny / automaton / advisory | recorded in `engine_report` only (not injected) | the agent may still raise it; does not auto-block |

A lone secret read, ordinary shell commands, and `WebSearch` (no destination host) produce **no**
violation — false-positive control carried over from the upstream build. (v1 injects only LOCKED
violations to keep the deterministic gate conservative; non-LOCKED signals are recorded for the agent
and a future tunable-severity pass.)

## Testing

- `tests/test_security_engines.py` (the repo's plain-Python subprocess pattern): `plan-extract.js` on
  an injected-plan fixture ⇒ an AST with a secret→sink flow; `verify_driver.py` on that AST ⇒ a
  LOCKED `no_secret_exfiltration` violation; a clean plan ⇒ `ok`; `run-cedar.js` on an exfil/destructive
  transcript ⇒ the two LOCKED flags; `emit-engine-report.js` fuses both; **`anchor-engine-findings.js`
  injects a LOCKED violation as a `critical` finding anchored to the diff's first added line and sets
  `verdict: REQUEST_CHANGES`**. Each engine sub-test is guarded on its toolchain (node / cedar-wasm /
  guardians+z3) so a runner missing a dep skips cleanly.
- Manual acceptance (live Actions run of `review-security-agent`) is the end-to-end path, per the
  protocol's convention — offline tests cannot exercise a real run.

## Non-goals / documented v1 limitations

- **No new protocol stage or `checks/` entry, no `protocol.json`/triage change.** Deterministic gating
  is achieved entirely within the security agent's post-step (anchored `findings[]`); triage is reused.
- **Guardians plan extraction is a deterministic heuristic** in v1 (pattern-detects declared/injected
  effects); LLM-assisted extraction is a follow-up.
- **Only LOCKED violations auto-gate** (injected as critical). Non-LOCKED denies/automaton/advisory are
  recorded in `engine_report` for the agent and a future tunable-severity injection pass.
- **Unanchored edge case:** if `pr.diff` has no added line (e.g. a pure-deletion PR), a LOCKED
  violation cannot be line-anchored — it is recorded in `engine_report` with `unanchored:true` and does
  not line-gate that round. Rare; documented.
- **Engine steps are fail-open** (a missing transcript/plan, or an absent dep, yields no engine
  findings — never a failed run), matching the protocol's non-fatal prefetch convention.
- Runner needs `node` (already set up for the codex agent) and **`python3.11`** (pinned via
  `actions/setup-python@v5`) with the installed deps; the agent's network firewall is unaffected
  (engines run in pre-firewall steps, offline).

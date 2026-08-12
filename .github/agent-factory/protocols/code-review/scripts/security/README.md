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

## `live-guard/` — real-time Cedar authorization on tool calls (phase B1)

A **second, independent** Cedar call site. Everything above authorizes *after the
fact* (a transcript or a self-declared `plan_ast`); `live-guard/` authorizes a tool call
**before it runs**, from a Claude Code `PreToolUse` hook. Design:
`docs/superpowers/specs/2026-08-10-realtime-cedar-tool-call-guard-design.md`.

```
stdin (PreToolUse JSON) → normalize.py → decide.js → _cedar-decide.js → {} | deny{reason}
                             (1..N)          ↑
                                    policy/cedar/live (vendored, 64)
```

One event normally means one Cedar request. codex's `apply_patch` is the
exception: its V4A envelope can describe several files, so it fans out into one
request per file operation and `hook.py` folds them — **any deny wins**. An
envelope naming NO file operation yields zero requests and is **denied**, not
allowed: `base.cedar` is default-permit, so evaluating nothing would consent by
omission. See `docs/STATUS.md` ("live-guard on codex").

| File | Role |
|---|---|
| `live-guard/normalize.py` | pure, importable. `normalize()`: event dict → one Cedar request (`Bash`→`ShellCommand`, `Read`→`FileRead`, `Write`→`FileWrite`, `Edit`/`NotebookEdit`→`FileEdit`, `WebFetch`→`WebFetch`, everything else→`PreToolUse`). `normalize_all()`: → the LIST of requests one event implies, scanning `apply_patch` V4A headers (`Add File`→`FileWrite`, `Update File`→`FileEdit`, `Delete File`→`FileDelete`, `Move to`→`FileWrite`+`FileDelete`) |
| `live-guard/decide.js` | thin driver: merge `policy/cedar/live` as a `{id: text}` policy map, call `decideDetailed()`, print `{decision, determining, descriptions, policy_count}` |
| `live-guard/hook.py` | the `PreToolUse` entrypoint; **fails open** by default, writes a per-session liveness record + a `decisions.jsonl` evidence log |
| `policy/cedar/live/` | vendored universal corpus (MIT, Sondera) + `base.cedarschema`; see its `README.md` |

Two vocabularies, deliberately not merged: the plan path speaks bare
`Action::"RunCommand"` with pre-computed booleans; the live path speaks
`Sondera::Action::"ShellCommand"` with *facts* (`command`, `path_normalized`).
Namespaces don't collide and neither driver reads the other's policy dir.

- **Not registered anywhere.** Registering the hook in `.claude/settings.json` is a
  manual, opt-in step (`hooks.PreToolUse[].hooks[].command` → `live-guard/hook.py`).
- **Failure posture is config, not code:** `CEDAR_LIVE_GUARD_FAILURE_MODE=open|closed`
  (default `open` — a guard bug must not wedge the session it governs). Because
  fail-closed cannot detect *"never installed"*, every session gets a **positive
  liveness record** under `$CEDAR_LIVE_GUARD_LOG_DIR/liveness/<session>.json`; its
  absence means **unenforced**, never "nothing to report".
- **Known B1 gaps** (asserted in tests, not assumed): the corpus has no
  `git push --force` policy; `context.parse` is stubbed `{ok:false}` so wrapped forms
  (`/bin/rm -r -f`) slip through until phase B3; no exfiltration rule until B2.
- Tests: `tests/protocols/code-review/test_live_guard_{normalize,policies,decide,hook}.py`.

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

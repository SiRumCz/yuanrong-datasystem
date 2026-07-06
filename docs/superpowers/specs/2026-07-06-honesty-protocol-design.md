# Design: `code-review-honesty` protocol (factory self-honesty)

**Date:** 2026-07-06
**Branch:** `feat/honesty-protocol`
**Status:** approved design → implementation planning

## Summary

Add a new agent-factory protocol, `code-review-honesty`, as a fork of
`code-review-fix`. Where `code-review-fix` fixes a review finding, the honesty
protocol audits the factory's **own** claims — specifically, whether the fix
loop's committed change actually did what it claimed and whether the issue it
closed was genuinely resolved. This is a *factory self-honesty* /
anti-reward-hacking check.

The protocol keeps `code-review-fix`'s exact two-state skeleton
(`triage → fix`), reuses its engine plumbing (`conclude-*`, schemas, checks)
**verbatim**, and expresses all honesty-specific behavior in two renamed agent
workflows. `code-review-fix` and its agents are left byte-for-byte untouched.

## Goals

- A runnable protocol triggered by `/honesty` on a PR comment that keys the run
  `pr-<N>` (same as `/fixit`).
- Audit subject: the fix loop's own output on that PR (did the committed change
  implement the claimed fix; was the right `[ai-review]` issue genuinely
  resolved).
- Zero changes to `protocols/code-review-fix/` or its agents
  (`fix-triage-agent`, `fix-agent`).

## Non-goals

- No new concluder and no schema/check changes — `conclude-fix`,
  `conclude-triage`, `_apply_fixes`, `_derive_gate`, and the evidence schemas
  are copied and reused unchanged.
- No review fanout, no preflight, no gate — same shape as `code-review-fix`.
- Not auditing PR-description accuracy, test-run claims, or agent transcripts
  (those were considered and deferred; see "Deferred").

## Key mechanical facts (verified against source)

1. **Agent dispatch is a flat global namespace.** `agentic-engine.yml:412-417`
   does `WF="$NAME.lock.yml"; gh workflow run "$WF"`, where `$NAME` is a state's
   `workflow:` value. So `workflow:` always resolves to
   `.github/workflows/<name>.lock.yml`; GitHub Actions only runs workflows from
   that one flat directory. Agents cannot live under `protocols/<name>/` and be
   dispatched. Same `workflow:` name = same lock file = same behavior.
   **Consequence:** to give the honesty protocol its own agent behavior while
   leaving `code-review-fix` untouched, the agents must be **new files with
   distinct names**. Hence the rename to `honesty-triage-agent` /
   `honesty-fix-agent`.

2. **Routing is auto-discovered.** `agentic-orchestrator.yml` declares the union
   of entry triggers (`issue_comment` already included) and a read-only `route`
   job scans `protocols/*/protocol.json` `triggers` to pick the protocol
   (`lib.py route`). A new protocol with a unique `comment_prefix` is picked up
   with **no orchestrator edit**. Prefixes must be mutually exclusive (none a
   prefix of another). `/honesty` is unique against the existing set
   (`/demo-review /factory /fast-mrp /fixit /impl-feature-auto /mm-answer
   /override /overview /review /rphase`).

3. **`.install.json` is an install-time integrity manifest**, not read by any CI
   workflow. Refreshing it for the new files is good hygiene but does not gate
   the pipeline.

4. **`conclude-fix` closes issues by exact title.** `conclude-fix.py:340` builds
   `expected_title = f"[ai-review][{dim}] {target_title}"`. Because we reuse
   `conclude-fix` verbatim, the honesty protocol inherits "commit to the PR head
   + close the matching `[ai-review]` issue by title." All honesty-specific
   logic must therefore live in the two agents' prompts + the evidence they
   emit; the plumbing is identical to `code-review-fix`.

## Architecture

New directory `protocols/code-review-honesty/`, a copy of
`protocols/code-review-fix/`:

```
protocols/code-review-honesty/
  protocol.json              # edited: name, /honesty trigger, agent refs
  triage.evidence.schema.json    # copied verbatim
  fix.evidence.schema.json       # copied verbatim
  checks/
    evidence-present.py          # copied verbatim
    triage-schema-valid.py       # copied verbatim
    fix-schema-valid.py          # copied verbatim
  publish/
    conclude-triage.py           # copied verbatim
    conclude-fix.py              # copied verbatim
    _apply_fixes.py              # copied verbatim
    _derive_gate.py              # copied verbatim
```

Two new agent workflows in `.github/workflows/` (renamed forks; each is a
`.md` source compiled to a `.lock.yml`):

- `honesty-triage-agent.{md,lock.yml}` — fork of `fix-triage-agent`. Reads the
  PR's closed/open `[ai-review]` issues **and** the committed change on the PR
  head, and emits a single-cluster triage identifying where the factory's claim
  does or does not match reality.
- `honesty-fix-agent.{md,lock.yml}` — fork of `fix-agent`. Consumes the triage
  and produces the remediation evidence that `conclude-fix` acts on.

`protocol.json` edits (only these):

- `name`: `code-review-fix` → `code-review-honesty`
- `_note`: describe the honesty audit
- `triggers[0].comment_prefix`: `/fixit` → `/honesty`
- state `triage`.`workflow`: `fix-triage-agent` → `honesty-triage-agent`
- state `fix`.`workflow`: `fix-agent` → `honesty-fix-agent`
- state ids stay `triage` and `fix`; `conclude`, `checks`, `evidence`,
  `inputs`, `next` unchanged.

## Data flow

1. `/honesty` comment on PR #N → orchestrator `route` matches
   `code-review-honesty` → engine starts run keyed `pr-<N>`.
2. `triage` state → `honesty-triage-agent` writes `evidence.json`
   (triage schema) → `evidence-present` + `triage-schema-valid` checks →
   `conclude-triage`.
3. `fix` state → `honesty-fix-agent` (takes triage as input) writes
   `evidence.json` (fix schema) → `evidence-present` + `fix-schema-valid`
   checks → `conclude-fix` resolves the PR head via `gh pr view`, applies +
   commits, and closes the matching `[ai-review]` issue by title → `done`.

## Testing / verification

- `agent-factory-tests.yml` and `engine/protocol-lint.py` must pass for the new
  protocol.json (agent states carry `workflow`; schema references resolve).
- Confirm the router selects `code-review-honesty` for a `/honesty` body and
  does not ambiguously match another protocol.
- Confirm each new agent `.lock.yml` is a valid compiled gh-aw workflow
  (recompiled from its `.md`, not hand-edited).

## Deferred (explicitly out of scope)

- Broader audit subjects: PR-description/commit-message accuracy, "tests
  pass/ran" claims, per-agent evidence/transcript audits.
- An annotate-style concluder (comment / reopen / label) instead of the reused
  commit+close-issue behavior. Revisit only if the inherited `conclude-fix`
  semantics prove wrong for honesty remediation.

## Open risk

Because `conclude-fix` is reused verbatim, "remediation" is commit-to-PR +
close-`[ai-review]`-issue. If the intended honesty outcome is really "flag /
reopen / annotate" rather than "commit a change," that is the one place the
current decisions (keep `triage → fix`, don't build a new concluder) would need
to be revisited. Captured here so the tradeoff is visible before implementation.

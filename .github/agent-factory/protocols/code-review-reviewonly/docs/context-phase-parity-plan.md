# Context Phase Parity Implementation Plan

## Current State

The current `context-agent` workflow fetches `.conversations/*.jsonl` from the PR head but lets the
agent parse raw transcript JSONL, classify, aggregate, and write `/tmp/gh-aw/evidence.json`.

That does not match custody's context export pipeline. Custody's model is deterministic parse and
assembly around an agent that emits only per-part phase labels.

## Target File Layout

```text
.github/agent-factory/protocols/code-review/
  docs/
    context-phase-parity-spec.md
    context-phase-parity-plan.md
  scripts/
    context/
      VENDORED.md
      locate.js
      assemble.js
      to-evidence.py
      parts-driver/
        driver.ts
        package.json
        bun.lock
        cv/...
  checks/
    context-schema-valid.py
  publish/
    conclude-context.py
```

## Work Slices

### Slice 1: Vendored Custody Runtime

Owner: subagent worker.

Write scope:

- `.github/agent-factory/protocols/code-review/scripts/context/locate.js`
- `.github/agent-factory/protocols/code-review/scripts/context/assemble.js`
- `.github/agent-factory/protocols/code-review/scripts/context/parts-driver/**`
- `.github/agent-factory/protocols/code-review/scripts/context/VENDORED.md`
- optional local fixtures under `.github/agent-factory/protocols/code-review/tests/fixtures/context/`

Tasks:

- Copy custody runtime files from `/home/haoxiang/workspace/custody/app/backend/component/context/workflow/scripts/`.
- Keep vendored files byte-identical where practical.
- Add `VENDORED.md` documenting source path, source branch/commit if discoverable, runtime dependencies, and edit policy.
- Do not edit workflow or protocol files.

### Slice 2: Evidence Adapter And Validation

Owner: subagent worker.

Write scope:

- `.github/agent-factory/protocols/code-review/scripts/context/to-evidence.py`
- `.github/agent-factory/protocols/code-review/checks/context-schema-valid.py`
- adapter/check tests or fixtures under `.github/agent-factory/protocols/code-review/tests/`

Tasks:

- Implement deterministic `SessionExport -> evidence.json`.
- Validate closed phase set, non-negative integer counts, and `transcript_present` consistency.
- Keep `context-schema-valid.py` advisory-compatible: print one JSON verdict and exit 0.
- Do not edit workflow or protocol files.

### Slice 3: Workflow And Protocol Integration

Owner: main agent.

Write scope:

- `.github/workflows/context-agent.md`
- `.github/workflows/context-agent.lock.yml`
- `.github/agent-factory/protocols/code-review/protocol.json`
- `.github/agent-factory/protocols/code-review/context.evidence.schema.json`
- `.github/agent-factory/protocols/code-review/publish/conclude-context.py`

Tasks:

- Rewrite `context-agent.md` to call protocol-local scripts.
- Expand bash tools to allow `node:*` and `bun:*`.
- Replace agent prompt with per-part `phases.jsonl` classification only.
- Upload both `context-export` and `evidence` artifacts.
- Add `conclude-context.py`, but keep `blocked:false`.
- Use `context-schema-valid` plus `evidence-present` as advisory checks.
- Recompile lock with `gh aw compile .github/workflows/context-agent.md`.

## Sequencing

1. Create spec and plan docs.
2. Spawn Slice 1 and Slice 2 workers in parallel.
3. Main agent edits workflow/protocol shell once script paths are stable.
4. Integrate worker changes.
5. Run targeted tests.
6. Compile workflow lock.
7. Self-verify with `.repo_context/playbooks/upkeep/ai-self-verification.md`.

## Test Commands

Expected targeted commands after implementation:

```bash
node --test .github/agent-factory/protocols/code-review/tests/context-locate.test.js
node --test .github/agent-factory/protocols/code-review/tests/context-assemble.test.js
python3 .github/agent-factory/protocols/code-review/scripts/context/to-evidence.py <session-export.json> <evidence.json>
python3 .github/agent-factory/protocols/code-review/checks/context-schema-valid.py <evidence.json> /dev/null /dev/null
gh aw compile .github/workflows/context-agent.md
```

If Bun/tiktoken dependencies cannot be installed locally, record that the parts-driver path remains
live-workflow validated only.

## Risk Gates

- Hot path impact: none; CI/review automation only.
- Concurrency impact: none in product runtime; workflow post-step ordering matters.
- Persistence/recovery impact: no product durable state; artifact generation must be deterministic and
  no-transcript-safe.
- Availability impact: context state must remain advisory and must not halt `mrp`.
- Security impact: transcript fetch runs with read-only `GITHUB_TOKEN`; agent has no GitHub network access.

# Context Phase Parity Spec

## Scope

This spec covers the `context` state in `.github/agent-factory/protocols/code-review/protocol.json`
and the `context-agent` workflow. The goal is to make the migrated code-review protocol emit
custody-compatible context composition artifacts while staying advisory in the engine pipeline.

## Source Of Truth

- Runtime source: the current repository workflow and protocol files.
- Parity source: `/home/haoxiang/workspace/custody/app/backend/component/context/workflow/scripts/`.
- Transcript source: `.conversations/*.jsonl` committed in the PR head tree.
- Parser/token source: custody's vendored context-viewer driver under `parts-driver/`.

## Required Artifacts

The context workflow must emit both artifacts on every run where post-steps execute:

- `/tmp/gh-aw/session-export.json`, uploaded as artifact `context-export`.
- `/tmp/gh-aw/evidence.json`, uploaded as artifact `evidence` for the code-review engine.

`session-export.json` must follow the custody/context-viewer `SessionExport` shape:

- `version: "1.0"`
- `files[].conversation.messages[].parts[].component`
- `files[].colors`
- `analytics.componentComparison[]`
- `meta.pr_number`
- `meta.head_sha`
- `error` object for empty or missing parse inputs

`evidence.json` must be derived deterministically from `session-export.json`, not from agent-authored
aggregate counts.

## Pipeline Contract

The workflow must use this split:

1. Trusted pre-agent step fetches PR metadata into `/tmp/gh-aw/agent/pr.json`.
2. Trusted pre-agent step runs protocol-local `scripts/context/locate.js` to fetch transcript files
   into `/tmp/gh-aw/agent/transcripts/`.
3. Trusted pre-agent step runs protocol-local `scripts/context/parts-driver/driver.ts` under Bun to
   parse transcripts into `/tmp/gh-aw/agent/parts.json`.
4. Agent reads only `parts.json` and appends one JSON object per classified part to
   `/tmp/gh-aw/agent/phases.jsonl`.
5. Trusted post-step runs protocol-local `scripts/context/assemble.js` to build
   `/tmp/gh-aw/session-export.json`.
6. Trusted post-step runs protocol-local adapter to build `/tmp/gh-aw/evidence.json` from
   `/tmp/gh-aw/session-export.json`.

The agent must not parse raw JSONL and must not write aggregate phase counts.

## Phase Semantics

Allowed phases:

- `UNDERSTAND`
- `EXPLORE`
- `ANALYZE`
- `PLAN`
- `IMPLEMENT`
- `VERIFY`
- `COMPLETE`

The agent output file must contain one JSON object per line:

```json
{"id":"<part id>","phase":"EXPLORE"}
```

The join key is the exact string `id` from `parts.json`. Missing, malformed, or unknown phases are
normalized by `assemble.js` to `COMPLETE`.

## Custody Runtime Parity

The following custody files should be copied as vendored runtime code and kept in their original
language/runtime:

- `locate.js`
- `assemble.js`
- `parts-driver/driver.ts`
- `parts-driver/package.json`
- `parts-driver/bun.lock`
- `parts-driver/cv/**`

Reason: `parts-driver` uses context-viewer's Claude parser and `tiktoken@1.0.22` (`gpt-4o`) for
token counting. Rewriting this in Python would create a second parser and break byte-for-byte parity.

Repo-specific glue may be Python when it follows the engine ABI:

- `checks/context-schema-valid.py`
- `publish/conclude-context.py`
- `scripts/context/to-evidence.py`

## Evidence Shape

The derived engine evidence must contain:

```json
{
  "transcript_present": true,
  "phases": [
    {"phase":"EXPLORE","token_count":10,"message_count":2}
  ],
  "meta": {"pr_number": 1, "head_sha": "abc"},
  "session_export": {"path": "/tmp/gh-aw/session-export.json", "error": false}
}
```

Rules:

- `phases` order must follow first appearance in `analytics.componentComparison[0].componentTokens`.
- `token_count` is derived from `componentTokens`.
- `message_count` is the count of parts assigned to that phase.
- `transcript_present` is `true` only when the export has at least one message part and no top-level
  `error`.
- Empty/no-transcript exports still produce valid evidence with `transcript_present:false`.

## Advisory Engine Behavior

The `context` state remains advisory:

- Context checks must never block progression to `mrp`.
- `conclude-context.py` may report neutral/clear status but must set `blocked:false`.
- Publish behavior is not required for the first parity slice unless the engine invocation path is
  proven for root-state publish hooks.

## Operational Risk

This change does not touch product request paths, durable cache metadata, worker/master runtime,
or distributed recovery behavior. Risk is limited to CI availability and review-pipeline artifact
correctness:

- GitHub Contents API behavior and >1 MB transcript files.
- Bun install/runtime availability before the agent firewall.
- Deterministic artifact shape for downstream engine checks and artifacts.
- No-transcript and malformed-transcript degradation.

## Verification Requirements

Minimum verification:

- Node tests for `locate.js` behavior using dependency-injected probes/readers.
- Node tests for `assemble.js` using custody fixtures.
- Python tests or direct command tests for `to-evidence.py`.
- Python tests or direct command tests for `context-schema-valid.py`.
- `gh aw compile .github/workflows/context-agent.md`.

Live workflow validation is still needed for the Bun/tiktoken path if local Bun dependencies cannot
be installed in the test environment.

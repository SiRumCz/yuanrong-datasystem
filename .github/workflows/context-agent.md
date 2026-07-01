---
name: "Context Agent (protocol state: context)"
run-name: "Context Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway below
  # (Tailscale Funnel, reachable from GitHub runners). gh-aw injects OPENAI_API_KEY
  # (repo secret). The agent needs no GitHub network access — the transcript is
  # prefetched in steps: (outside the agent firewall).
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
    # codex's `defaults` omits the gateway host.
    - arcyleung-ubuntu.tailb940e6.ts.net
permissions:
  contents: read
  pull-requests: read
  issues: read
safe-outputs:
  staged: true
  noop: {}
tools:
  bash: [ "cat:*", "echo:*", "node:*", "bun:*" ]
  edit:
steps:
  # The repo must be checked out into the workspace ROOT — gh-aw's agent job runs
  # "Configure Git credentials" before its own checkout, so a root .git must exist.
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Prefetch PR + locate transcript + parse parts
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent /tmp/gh-aw/agent/transcripts
      gh pr view "$PR" --repo "$REPO" --json number,title,author,files,baseRefName,headRefName,headRefOid > /tmp/gh-aw/agent/pr.json
      REPO="$REPO" node .github/agent-factory/protocols/code-review/scripts/context/locate.js /tmp/gh-aw/agent/pr.json /tmp/gh-aw/agent/transcripts || true
      if ls /tmp/gh-aw/agent/transcripts/*.jsonl >/dev/null 2>&1; then
        # gh-aw can order setup actions after custom steps. Install Bun inline while
        # the trusted pre-agent step still has network access.
        command -v bun >/dev/null 2>&1 || { npm install -g bun >/dev/null 2>&1 && export PATH="$(npm prefix -g)/bin:$PATH"; } || true
        ( cd .github/agent-factory/protocols/code-review/scripts/context/parts-driver \
          && (bun install --frozen-lockfile || bun install) \
          && bun driver.ts /tmp/gh-aw/agent/transcripts /tmp/gh-aw/agent/parts.json ) || true
      fi
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cat /tmp/gh-aw/task-context.json
post-steps:
  - name: Assemble SessionExport
    if: always()
    run: node .github/agent-factory/protocols/code-review/scripts/context/assemble.js /tmp/gh-aw/agent/parts.json /tmp/gh-aw/agent/phases.jsonl /tmp/gh-aw/agent/pr.json > /tmp/gh-aw/session-export.json
  - name: Derive engine evidence
    if: always()
    run: python3 .github/agent-factory/protocols/code-review/scripts/context/to-evidence.py /tmp/gh-aw/session-export.json /tmp/gh-aw/evidence.json
  - name: Upload session export
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: context-export
      path: /tmp/gh-aw/session-export.json
      retention-days: 7
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 10
source: golivax/agentic-protocol-poc/.github/workflows/context-agent.md@6e591f728eedbcf694b79c435dc5e6147c1d882c
---

# Context Composition - 7-phase transcript classification

You classify pre-parsed Claude-Code transcript parts into agent-workflow phases.
Your ONLY output is `/tmp/gh-aw/agent/phases.jsonl`, then you call `noop`. Do not
post comments or use any other output.

## Data source

The trusted pre-agent step locates the PR's committed `.conversations/*.jsonl`
files at the PR head, parses them with the vendored context-viewer driver, and
writes `/tmp/gh-aw/agent/parts.json`.

`parts.json` has a top-level `messages` array. Each message has a `parts` array.
Each part has an `id`, a `type` (`text` / `reasoning` / `tool-call` /
`tool-result`), a `token_count`, and content (`text`, or `toolName` plus
`input`/`output`).

`/tmp/gh-aw/task-context.json` carries `pr`, `iteration`, and `feedback`.

## Steps

1. Check `/tmp/gh-aw/agent/parts.json`. If it is absent or has no parts, write
   nothing, call `noop`, and stop. The post-step emits a deterministic no-transcript
   export and evidence. Do NOT fabricate data.

2. If parts are present, read every part across all messages. Classify each part
   into exactly one phase from this closed set of 7:

   - **UNDERSTAND** - comprehending the task requirements/constraints (early user-intent reasoning)
   - **EXPLORE** - Read/Grep/Glob/search tool calls; reading files; gathering context
   - **ANALYZE** - reasoning parts: root cause, weighing tradeoffs, designing an approach
   - **PLAN** - TodoWrite / planning; laying out actionable steps
   - **IMPLEMENT** - Edit/Write/MultiEdit tool calls; code changes
   - **VERIFY** - Bash running tests/lint/build/type-checks; reading their results
   - **COMPLETE** - final summary, cleanup, closing message

   Base each label on real part content, message role, and tool names. Fold any
   prior `feedback` from the task context into this pass.

3. Append one JSON object per line to `/tmp/gh-aw/agent/phases.jsonl`, using each
   part's exact `id`:

   ```
   {"id":"<part id>","phase":"EXPLORE"}
   ```

   Emit exactly one line per part in `parts.json`. For a large transcript, process
   the parts in chunks so every part gets exactly one line.

4. Write nothing else, then call `noop`.

---
on:
  workflow_dispatch:
    inputs:
      pr_number: { description: "PR number", required: true }
      repo:      { description: "owner/name (defaults to this repo)", required: false }
permissions: { contents: read, pull-requests: read }
engine:
  id: codex
  model: gpt-5.5
  # Codex routed through the private OpenAI-compatible gateway (same as preflight-gate.md).
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
    - arcyleung-ubuntu.tailb940e6.ts.net
safe-outputs: { staged: true, noop: {} }
tools:
  bash: [ "node:*", "bun:*", "cat:*", "echo:*", "git:*" ]
  edit:
steps:
  - name: Checkout (scripts live in this repo)
    uses: actions/checkout@v4
    with: { persist-credentials: false }
  - name: Prefetch PR + locate transcript + parse parts
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ github.event.inputs.pr_number }}", REPO: "${{ github.event.inputs.repo || github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,author,files,baseRefName,headRefName,headRefOid > /tmp/gh-aw/agent/pr.json
      REPO="$REPO" node app/backend/component/context/workflow/scripts/locate.js /tmp/gh-aw/agent/pr.json /tmp/gh-aw/agent/transcript.jsonl || true
      if [ -f /tmp/gh-aw/agent/transcript.jsonl ]; then
        # gh-aw's compiler hoists a separate `uses: oven-sh/setup-bun` step to AFTER this one, so bun
        # isn't on PATH yet. Install it inline instead (node/npm are already present — locate.js just
        # ran under node — and the network is open here, before the agent firewall starts), so the
        # parts-driver always has bun regardless of how gh-aw orders setup steps.
        command -v bun >/dev/null 2>&1 || { npm install -g bun >/dev/null 2>&1 && export PATH="$(npm prefix -g)/bin:$PATH"; } || true
        ( cd app/backend/component/context/workflow/scripts/parts-driver && (bun install --frozen-lockfile || bun install) \
          && bun driver.ts /tmp/gh-aw/agent/transcript.jsonl /tmp/gh-aw/agent/parts.json ) || true
      fi
post-steps:
  - name: Assemble SessionExport
    if: always()
    run: node app/backend/component/context/workflow/scripts/assemble.js /tmp/gh-aw/agent/parts.json /tmp/gh-aw/agent/phases.jsonl /tmp/gh-aw/agent/pr.json > /tmp/gh-aw/session-export.json
  - name: Upload session export
    if: always()
    uses: actions/upload-artifact@v4
    with: { name: context-export, path: /tmp/gh-aw/session-export.json, retention-days: 7 }
---

You are the Context Composition Exporter. Classify every transcript part into exactly one agent-workflow phase.

## Data source

The pre-parsed transcript parts are at `/tmp/gh-aw/agent/parts.json`. It has a top-level
`messages` array; each message has a `parts` array; each part has an `id`, a `type`
(`text` / `reasoning` / `tool-call` / `tool-result`), a `token_count`, and content
(`text`, or `toolName` + `input`/`output`).

**If `/tmp/gh-aw/agent/parts.json` is absent or has no parts, write nothing and call `noop`** —
the post-step emits an informative error export. Do not fabricate data.

## Task — classify every part

Read `/tmp/gh-aw/agent/parts.json`. For **every** part across all messages, assign **exactly one**
label from this closed set:

- **UNDERSTAND** — comprehending the task requirements/constraints (early user-intent reasoning)
- **EXPLORE** — Read/Grep/Glob/search tool calls; reading files; gathering context
- **ANALYZE** — reasoning parts: root cause, weighing tradeoffs, designing an approach
- **PLAN** — TodoWrite / planning; laying out actionable steps
- **IMPLEMENT** — Edit/Write/MultiEdit tool calls; code changes
- **VERIFY** — Bash running tests/lint/build/type-checks; reading their results
- **COMPLETE** — final summary, cleanup, closing message

Append **one JSON object per line** to `/tmp/gh-aw/agent/phases.jsonl`, using each part's **exact `id`**:

```
{"id":"<part id>","phase":"EXPLORE"}
```

Emit exactly one line per part in `parts.json`. For a large transcript, process the parts in
chunks so you classify every part. When finished, call `noop`.

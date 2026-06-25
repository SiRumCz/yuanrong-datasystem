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
  bash: [ "cat:*", "echo:*" ]
  edit:
steps:
  # The repo must be checked out into the workspace ROOT — gh-aw's agent job runs
  # "Configure Git credentials" before its own checkout, so a root .git must exist.
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Prefetch PR transcript from conversations branch (best-effort)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent/conv
      OWNER="${REPO%%/*}"
      NAME="${REPO##*/}"
      # The committed Claude-Code transcript for this PR lives on the `conversations`
      # branch under $OWNER/$NAME/pr-$N/ as one or more *.jsonl session files. List
      # that directory and base64-decode each *.jsonl into /tmp/gh-aw/agent/conv/.
      # Best-effort: a PR with no committed transcript yields an empty conv/ dir (a
      # legal clean absence — the agent emits an empty attestation, never fabricates).
      python3 - "$REPO" "$OWNER" "$NAME" "$PR" <<'PY'
      import base64, json, os, subprocess, sys
      repo, owner, name, pr = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
      path = f"{owner}/{name}/pr-{pr}"
      out = subprocess.run(
          ['gh', 'api', f'repos/{repo}/contents/{path}?ref=conversations'],
          capture_output=True, text=True)
      if out.returncode != 0 or not out.stdout.strip():
          # 404 / empty: clean absence — leave conv/ empty.
          sys.stderr.write(f"prefetch: no transcript dir at {path} (clean absence)\n")
          sys.exit(0)
      try:
          entries = json.loads(out.stdout)
      except Exception as e:
          sys.stderr.write(f"prefetch: could not parse contents listing: {e}\n")
          sys.exit(0)
      n = 0
      for e in entries:
          if e.get('type') != 'file' or not str(e.get('name', '')).endswith('.jsonl'):
              continue
          fout = subprocess.run(
              ['gh', 'api', f"repos/{repo}/contents/{e['path']}?ref=conversations", '--jq', '.content'],
              capture_output=True, text=True)
          if fout.returncode != 0 or not fout.stdout.strip():
              continue
          try:
              data = base64.b64decode(fout.stdout.strip()).decode('utf-8', 'replace')
          except Exception:
              continue
          open(f"/tmp/gh-aw/agent/conv/{n:03d}.jsonl", 'w').write(data)
          n += 1
      sys.stderr.write(f"prefetch: wrote {n} session file(s) to /tmp/gh-aw/agent/conv/\n")
      PY
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cat /tmp/gh-aw/task-context.json
post-steps:
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 10
---

# Context Composition — 7-phase transcript classification

You read a committed Claude-Code transcript for this PR and classify its parts
into agent-workflow phases. Your ONLY output is one JSON object written to
`/tmp/gh-aw/evidence.json` via the `edit` tool, then you call `noop`. Do not post
comments or use any other output.

## Data source

The prefetch step base64-decodes the PR's committed transcript session files (if
any) into `/tmp/gh-aw/agent/conv/` as `*.jsonl`. Each file is JSON-Lines: one JSON
record per line. A record is typically a Claude-Code message with a `type`
(e.g. `user`, `assistant`) and content (text and/or tool-use / tool-result
entries). `/tmp/gh-aw/task-context.json` carries `pr`, `iteration`, and `feedback`.

## Steps

1. Check `/tmp/gh-aw/agent/conv/` for `*.jsonl` files (`cat` them). **If there are
   no transcript files**, write evidence `{"phases": [], "transcript_present": false}`
   with the `edit` tool — this is a legal empty attestation, NOT a failure — then
   call `noop` and stop. Do NOT fabricate a transcript or invent phases.

2. If transcript files are present, read every record across all session files.
   Classify each transcript message/part into **exactly one** phase from this
   closed set of 7:

   - **UNDERSTAND** — comprehending the task requirements/constraints (early user-intent reasoning)
   - **EXPLORE** — Read/Grep/Glob/search tool calls; reading files; gathering context
   - **ANALYZE** — reasoning: root cause, weighing tradeoffs, designing an approach
   - **PLAN** — TodoWrite / planning; laying out actionable steps
   - **IMPLEMENT** — Edit/Write/MultiEdit tool calls; code changes
   - **VERIFY** — Bash running tests/lint/build/type-checks; reading their results
   - **COMPLETE** — final summary, cleanup, closing message

   Base each label on real content from the records (tool names, message role,
   text). Fold any prior `feedback` from the task context into this pass.

3. Write `/tmp/gh-aw/evidence.json` as ONE JSON object, using the `edit` tool:

   ```
   {"phases":[{"phase":"<PHASE>","message_count":<int>}],"transcript_present":true,"summary":"<one-line overview of the conversation arc>"}
   ```

   - Emit one `phases` entry per phase that actually occurred, in the order the
     phases first appear. `message_count` is how many transcript messages/parts you
     assigned to that phase (you may instead report `token_count` if you have a
     reliable count; message counts are the expected BASIC measure).
   - `summary` is a short prose description of how the conversation moved through
     the phases.

   Write nothing else, then call `noop`.

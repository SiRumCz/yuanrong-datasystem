---
name: "Honesty Fix-Verify Agent (protocol state: honesty.fixverify)"
run-name: "Honesty Fix-Verify · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
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
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Deterministic fix-claim check (writes evidence outside the agent firewall)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/pr.diff || true
      gh issue list --repo "$REPO" --label ai-review --state all \
        --json number,title,body,state,url --limit 100 > /tmp/gh-aw/issues.json || echo '[]' > /tmp/gh-aw/issues.json
      python3 - "$PR" <<'PY'
      import json, re, sys
      pr = sys.argv[1]
      issues = json.load(open("/tmp/gh-aw/issues.json"))
      diff = open("/tmp/gh-aw/pr.diff").read()
      # Added (RIGHT-side) lines in the committed diff.
      added = "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
      # In-scope: [ai-review] issues whose body references this PR.
      scoped = [i for i in issues if f"PR #{pr}" in (i.get("body") or "")]
      closed = [i for i in scoped if i.get("state","").upper() == "CLOSED"]
      target = closed[0] if closed else (scoped[0] if scoped else None)
      def fix_block(body):
          m = re.search(r"\*\*Suggested fix\*\*\s*```(.*?)```", body or "", re.S)
          return (m.group(1).strip() if m else "")
      if not target:
          ev = {"check":"fixverify","pass":False,"reason":"no in-scope [ai-review] issue for this PR"}
      else:
          fix = fix_block(target.get("body",""))
          # Heuristic content anchor: the operator/token the suggested fix introduces (e.g. '>=').
          tok = None
          m = re.search(r"`([^`]*>=[^`]*)`", fix) or re.search(r"(>=)", fix)
          if m: tok = m.group(1)
          present = bool(tok) and (tok in added)
          if target.get("state","").upper()=="CLOSED" and not present:
              ev = {"check":"fixverify","pass":False,
                    "reason":f"issue #{target['number']} closed but its suggested fix ('{tok}') is not in the committed diff"}
          elif present:
              ev = {"check":"fixverify","pass":True,
                    "reason":f"suggested fix ('{tok}') present in committed diff; issue #{target['number']}"}
          else:
              ev = {"check":"fixverify","pass":True,
                    "reason":f"issue #{target['number']} not yet closed; nothing to catch"}
      json.dump(ev, open("/tmp/gh-aw/evidence.json","w"))
      print(json.dumps(ev))
      PY
      cat /tmp/gh-aw/evidence.json
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
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

# Honesty Fix-Verify — confirm the deterministic verdict

A pre-agent step already computed the honesty verdict deterministically and wrote it to
`/tmp/gh-aw/evidence.json` as `{ "check": "fixverify", "pass": <bool>, "reason": "<why>" }`
by comparing the closed `[ai-review]` issue's Suggested fix against the committed PR diff.

Do exactly this:
1. `cat /tmp/gh-aw/evidence.json`.
2. If the file exists and already has the three keys `check`, `pass`, `reason`, **do not change it** — the deterministic step is the source of truth. Write nothing.
3. Only if the file is missing or malformed, write `{ "check": "fixverify", "pass": false, "reason": "deterministic step produced no evidence" }` to `/tmp/gh-aw/evidence.json` using the `edit` tool.

Then call `noop`. Do NOT re-derive the verdict yourself, post comments, or use any other safe-output.

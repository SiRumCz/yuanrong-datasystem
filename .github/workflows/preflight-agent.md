---
name: "Preflight Agent (protocol state: preflight)"
run-name: "Preflight Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway below
  # (Tailscale Funnel, reachable from GitHub runners). gh-aw injects OPENAI_API_KEY
  # (repo secret). The agent needs no GitHub network access — PR data is prefetched
  # in steps: (outside the agent firewall).
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
  - name: Prefetch PR + scope adherence checks (diff + PR body)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,body,files,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
      # Scope which adherence checks to judge: those whose artifact is ASSOCIATED with
      # the PR — a changed spec/plan file, a body section, or (spec only) the PR
      # description as the claim. Uses the SAME shared _locate locator as the
      # deterministic adherence-coverage check, so the agent's scope and the coverage
      # gate agree. Read the artifact text (committed file, else the body slice) so the
      # agent can judge against it.
      python3 - "$REPO" <<'PY'
      import base64, json, os, subprocess, sys
      sys.path.insert(0, os.path.join(os.environ.get('GITHUB_WORKSPACE', '.'),
                                      '.github/agent-factory/protocols/code-review/checks'))
      import _locate
      repo = sys.argv[1]
      pr = json.load(open('/tmp/gh-aw/agent/pr.json'))
      head = pr.get('headRefOid') or ''
      body = pr.get('body') or ''
      # `gh pr view --json files` returns objects keyed `path`.
      files = [f['path'] for f in pr.get('files', [])]
      def read_file(path):
          out = subprocess.run(['gh', 'api', f'repos/{repo}/contents/{path}?ref={head}', '--jq', '.content'],
                               capture_output=True, text=True)
          if out.returncode != 0 or not out.stdout.strip(): return None
          try: return base64.b64decode(out.stdout.strip()).decode('utf-8')
          except Exception: return None
      ai = []
      for cid, kind in (('spec-adherence', 'spec'), ('plan-adherence', 'plan')):
          r = _locate.locate(kind, body, files)
          text = _locate.artifact_text(kind, body, r['changed_hits'], read_file) if r['found'] else ''
          open(f'/tmp/gh-aw/agent/{kind}.txt', 'w').write(text)
          if r['found']: ai.append(cid)
      open('/tmp/gh-aw/agent/ai-checks.json', 'w').write(json.dumps(ai))
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

# Preflight Gate — adherence judgment only

You judge ONLY spec/plan adherence. Deterministic facts (spec/plan/docs/tests
presence) are computed by the engine's checks — do NOT recompute them.

1. Read `/tmp/gh-aw/agent/ai-checks.json` (the check ids to judge). If it is `[]`,
   write evidence with an empty `checks` list (see step 4) — there is no artifact
   to judge against — then call `noop` and stop.
2. Read `/tmp/gh-aw/agent/pr.diff`, `/tmp/gh-aw/agent/spec.txt`, `/tmp/gh-aw/agent/plan.txt`,
   and `/tmp/gh-aw/task-context.json` (`pr`, `iteration`, `feedback` — fold prior
   feedback into this pass).
3. For each requested id, judge the diff against the located artifact text ONLY
   (never infer an artifact):
   - `spec-adherence`: does the diff achieve what `spec.txt` requires?
   - `plan-adherence`: does the diff follow `plan.txt`?
   status: pass = adheres, warn = partial, fail = does not. Base every verdict on
   real evidence from the diff.
4. Write `/tmp/gh-aw/evidence.json` (the engine evidence path) as ONE JSON object,
   using the `edit` tool:
   `{"checks":[{"id":"<id>","status":"pass|fail|warn","summary":"…","evidence":[{"label":"…","detail":"…"}],"remediation":"…"}], "examined":["<files you read in the diff>"]}`
   Include one `checks` entry per requested id; `examined` lists the changed files
   you inspected. Write nothing else, then call `noop`.

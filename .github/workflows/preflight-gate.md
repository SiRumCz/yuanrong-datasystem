---
on:
  workflow_dispatch:
    inputs:
      pr_number:   { description: "PR number", required: true }
      repo:        { description: "owner/name (defaults to this repo)", required: false }
      scripts_ref: { description: "custody ref to run analysis scripts from", required: false, default: "main" }
permissions: { contents: read, pull-requests: read, issues: read }
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway below
  # (Tailscale Funnel, reachable from GitHub runners). gh-aw injects OPENAI_API_KEY
  # (repo secret) — same setup as custody-workflow's daily-repo-status.md.
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
    # codex's `defaults` omits the gateway host; the agent itself needs no GitHub
    # network access (PR data is prefetched in steps:, outside the agent firewall).
    - arcyleung-ubuntu.tailb940e6.ts.net
safe-outputs: { staged: true, noop: {} }
tools:
  bash: [ "node:*", "cat:*", "echo:*" ]
  edit:
steps:
  - name: Checkout custody analysis scripts (pinned)
    uses: actions/checkout@v4
    with:
      repository: SiRumCz/custody
      ref: ${{ github.event.inputs.scripts_ref || 'main' }}
      token: ${{ secrets.CUSTODY_SCRIPTS_TOKEN }}
      path: _custody
      persist-credentials: false
      sparse-checkout: |
        app/backend/component/preflight/workflow
  - name: Prefetch + deterministic checks
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ github.event.inputs.pr_number }}", REPO: "${{ github.event.inputs.repo || github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,author,body,files,baseRefName,headRefName,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || {
        echo "::warning::pr diff too large; assembling per-file patches (may be partial)"
        gh api "repos/$REPO/pulls/$PR/files" --paginate \
          --jq '.[] | "diff --git a/\(.filename) b/\(.filename)\n--- a/\(.filename)\n+++ b/\(.filename)\n\(.patch // "(patch omitted: too large)")\n"' \
          > /tmp/gh-aw/agent/pr.diff
      }
      HEAD_SHA=$(jq -r .headRefName /tmp/gh-aw/agent/pr.json) REPO="$REPO" \
        node _custody/app/backend/component/preflight/workflow/scripts/deterministic-checks.js \
        /tmp/gh-aw/agent/pr.json /tmp/gh-aw/agent/pr.diff /tmp/gh-aw/agent > /tmp/gh-aw/agent/deterministic.json
post-steps:
  - name: Merge deterministic + AI → verdict.json
    if: always()
    run: node _custody/app/backend/component/preflight/workflow/scripts/merge-verdict.js /tmp/gh-aw/agent/deterministic.json /tmp/gh-aw/agent/ai-results.jsonl /tmp/gh-aw/agent/pr.json > /tmp/gh-aw/verdict.json
  - name: Upload gate result
    if: always()
    uses: actions/upload-artifact@v4
    with: { name: preflight-gate, path: /tmp/gh-aw/verdict.json, retention-days: 7 }
---

# Preflight Gate — adherence judgment only

You judge **only** the spec/plan ADHERENCE checks. Deterministic checks are computed separately — do
not recompute them.

1. Read `/tmp/gh-aw/agent/ai-checks.json` (the check ids to judge). If it is `[]`, write nothing to
   `ai-results.jsonl` and call `noop`.
2. Read `/tmp/gh-aw/agent/pr.json`, `/tmp/gh-aw/agent/pr.diff`, and the located artifacts
   `/tmp/gh-aw/agent/spec.txt` and `/tmp/gh-aw/agent/plan.txt`.
3. For each requested check id, judge whether the diff adheres to the located artifact:
   - `spec-adherence`: does the diff achieve what SPEC (spec.txt) requires?
   - `plan-adherence`: does the diff follow PLAN (plan.txt)?
   Judge ONLY against the provided artifact text; never infer an artifact. Base every verdict on real
   evidence from the diff.
4. Append ONE JSON object per requested check to `/tmp/gh-aw/agent/ai-results.jsonl` (one per line):
   `{"id":"<id>","status":"pass|fail|warn","summary":"…","evidence":[{"label":"…","detail":"…"}],"remediation":"…"}`
   (status: pass = adheres, warn = partial, fail = does not; remediation only for fail/warn).
   Use the bash `echo`/`edit` tools to write the file. Then call `noop`.

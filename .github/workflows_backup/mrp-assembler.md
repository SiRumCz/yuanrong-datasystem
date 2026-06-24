---
on:
  workflow_dispatch:
    inputs:
      pr_number:   { description: "PR number", required: true }
      repo:        { description: "owner/name (defaults to this repo)", required: false }
      scripts_ref: { description: "custody ref for analysis scripts", required: false, default: "main" }
permissions: { contents: read, pull-requests: read, issues: read, actions: read }
engine:
  id: codex
  model: gpt-5.5
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
    - arcyleung-ubuntu.tailb940e6.ts.net
safe-outputs: { staged: true, noop: {} }
tools:
  bash: [ "node:*", "cat:*", "jq:*", "echo:*", "ls:*" ]
  edit:
steps:
  - name: Checkout (repo workspace for the gh-aw agent + git)
    uses: actions/checkout@v4
    with: { persist-credentials: false }
  - name: Checkout custody analysis scripts (pinned)
    uses: actions/checkout@v4
    with:
      repository: SiRumCz/custody
      ref: ${{ github.event.inputs.scripts_ref || 'main' }}
      token: ${{ secrets.CUSTODY_SCRIPTS_TOKEN }}
      path: _custody
      persist-credentials: false
      sparse-checkout: |
        app/backend/component/mrp/workflow
        app/backend/core
  - name: Gather upstream artifacts + prefetch conversation
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ github.event.inputs.pr_number }}", REPO: "${{ github.event.inputs.repo || github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent/inputs /tmp/gh-aw/agent/conv
      HEAD_SHA=$(gh pr view "$PR" --repo "$REPO" --json headRefOid -q .headRefOid)
      node _custody/app/backend/component/mrp/workflow/scripts/gather.js "$REPO" "$PR" "$HEAD_SHA" /tmp/gh-aw/agent/inputs
      OWNER=${REPO%%/*}; NAME=${REPO##*/}
      for p in $(gh api "repos/$REPO/contents/$OWNER/$NAME/pr-$PR?ref=conversations" --jq '.[].path' 2>/dev/null || true); do
        case "$p" in *.jsonl) gh api "repos/$REPO/contents/$p?ref=conversations" --jq '.content' 2>/dev/null | base64 -d > "/tmp/gh-aw/agent/conv/$(basename "$p")" || true ;; esac
      done
      ls -la /tmp/gh-aw/agent/inputs
post-steps:
  - name: Assemble → mrp.json
    if: always()
    run: node _custody/app/backend/component/mrp/workflow/scripts/assemble-mrp.js /tmp/gh-aw/agent/inputs /tmp/gh-aw/agent/agent-out.json > /tmp/gh-aw/mrp.json
  - name: Upload MRP
    if: always()
    uses: actions/upload-artifact@v4
    with: { name: merge-readiness-pack, path: /tmp/gh-aw/mrp.json, retention-days: 7 }
---

# MRP Assembler — synthesize, do not re-review

You assemble the judgment slices of the Merge-Readiness Pack from the gathered evidence in
`/tmp/gh-aw/agent/inputs/` and the conversation transcript in `/tmp/gh-aw/agent/conv/`. You do NOT
re-review the code; you synthesize what the upstream gates already produced. A deterministic
post-step computes the **acceptance_plan** (rung per cohort + routed question) and writes the final `mrp.json` — you only write
`/tmp/gh-aw/agent/agent-out.json`.

1. Read `inputs/manifest.json` (which inputs are present) and every present input.
2. **Rationale** — from `inputs/overview.json` (the walkthrough: `summary` + `cohorts[].layers[]`)
   and the transcript files in `conv/`, write a clear-rationale object:
   `{ summary, keyPoints: [ { point, snippet, source: "conversation"|"walkthrough" } ], intentMatch: "aligned"|"partial"|"unclear" }`.
   Snippets are VERBATIM quotes (≤200 chars). If `conv/` is empty, derive from the walkthrough alone
   and set every `source` to `"walkthrough"`.
3. **routed_spots** — the SMALL set of must-look hunks: the highest-priority `inputs/triage.json`
   clusters plus any `hard-break` (`severityClass`) from `inputs/overview.json` cohorts. Each:
   `{ spot_id, cohort, diff_hunk_pointer, risk_source: "critique"|"trajectory" }`. Keep it small.
4. **critique_ledger** — from the five `inputs/{correctness,security,performance,test,maintainability}.json`
   and `inputs/triage.json` clusters: `{ dimension, path, line, severity, verdict, title, rationale,
   evidence, suggested_fix }`. Carry each finding's verdict category when present, else `"risk"`.
5. **routed_questions** — for each cohort in `inputs/overview.json` whose `band` is `High` or
   `Critical`, formulate ONE question, derived from that cohort's `critique_ledger` findings and
   walkthrough, that **can only be answered by reading the cohort's changed hunk** (not from the
   overview prose). Output as an object keyed by cohort name: `{ "<cohort>": "<question>" }`. Omit
   Low/Medium cohorts. Derive questions from existing findings — do not invent new issues.
6. Write ONE JSON object `{ rationale, routed_spots, critique_ledger, routed_questions }` to
   `/tmp/gh-aw/agent/agent-out.json` using the `edit` tool. Then call `noop`. Never write the repo.

**Anti-fabrication:** if an input is absent, leave its slice empty (`[]`) or omit it — never invent
findings, spots, or rationale you cannot ground in the gathered evidence.

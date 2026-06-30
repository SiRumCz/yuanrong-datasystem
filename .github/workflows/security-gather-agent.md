---
name: "Preflight: security-gather (protocol state: preflight.security-gather)"
run-name: "Security Gather · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway.
  # gh-aw injects OPENAI_API_KEY (repo secret). The noop call below means the
  # agent produces no LLM output — the evidence is assembled deterministically.
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
  - name: Prefetch PR metadata
    env:
      GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
      PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}"
      REPO: "${{ github.repository }}"
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" \
        --json number,title,author,body,files,baseRefName,headRefName,headRefOid \
        > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
  - name: Set up Python 3.11 for Guardians
    uses: actions/setup-python@v5
    with:
      python-version: '3.11'
  - name: Run Cedar + Guardians security engines (deterministic, fail-open)
    env:
      GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
      REPO: "${{ github.repository }}"
    run: |
      # Two off-the-shelf engines audit the change for security data-flow risks. Every line is
      # fail-open (|| true / fallback JSON): a missing transcript/plan/dep never fails the run.
      SEC=.github/agent-factory/protocols/code-review/scripts/security
      CTX=.github/agent-factory/protocols/code-review/scripts/context
      A=/tmp/gh-aw/agent
      HEAD_SHA=$(jq -r '.headRefOid // ""' "$A/pr.json" 2>/dev/null || echo "")
      # Captured agent transcript (.conversations/*.jsonl) via the protocol's own locator → Cedar input.
      node "$CTX/locate.js" "$A/pr.json" "$A/transcripts" || true
      # Plan text: derive the plan path from changed files, fetch at head → Guardians input.
      PLAN_PATH=$(jq -r '[.files[].path] | map(select(test("(?i)(docs/.*plans?/|^plans?/|PLAN\\.md$)"))) | .[0] // ""' "$A/pr.json" 2>/dev/null || echo "")
      if [ -n "$PLAN_PATH" ]; then gh api "repos/$REPO/contents/$PLAN_PATH?ref=$HEAD_SHA" --jq '.content' 2>/dev/null | base64 -d > "$A/plan.txt" || true; fi
      [ -s "$A/plan.txt" ] || : > "$A/plan.txt"
      # Optional per-repo DECLARATIVE custom policy (data only — never executed). LOCKED rules win.
      CCDIR="$A/custom-cedar"; mkdir -p "$CCDIR"
      for fn in $(gh api "repos/$REPO/contents/.custody/policy/cedar?ref=$HEAD_SHA" --jq '.[].name' 2>/dev/null || true); do
        case "$fn" in *.cedar) gh api "repos/$REPO/contents/.custody/policy/cedar/$fn?ref=$HEAD_SHA" --jq '.content' 2>/dev/null | base64 -d > "$CCDIR/$fn" || true ;; esac
      done
      CUSTOM_CEDAR=""; [ -n "$(ls -A "$CCDIR" 2>/dev/null)" ] && CUSTOM_CEDAR="$CCDIR"
      CUSTOM_GUARD=""
      if gh api "repos/$REPO/contents/.custody/policy/guardians.policy.yaml?ref=$HEAD_SHA" --jq '.content' 2>/dev/null | base64 -d > "$A/custom-guardians.yaml" 2>/dev/null; then CUSTOM_GUARD="$A/custom-guardians.yaml"; fi
      # Install the engines (fail-open).
      ( cd "$SEC" && npm install --no-audit --no-fund --silent ) || true
      python3.11 -m pip install --quiet "git+https://github.com/metareflection/guardians@main" z3-solver pydantic pyyaml || true
      # Run: Cedar over the transcript; plan → AST → Guardians; fuse → engine-report.json.
      CHANGED=$(jq -c '[.files[].path] // []' "$A/pr.json" 2>/dev/null || echo '[]')
      node "$SEC/run-cedar.js" "$SEC/policy/cedar/default" "$CUSTOM_CEDAR" "$A/transcripts" "$CHANGED" > "$A/cedar.json" 2>/dev/null || echo '{"status":"n/a","flags":[]}' > "$A/cedar.json"
      node "$SEC/plan-extract.js" "$A/plan.txt" > "$A/gx-workflow.json" 2>/dev/null || echo '{"steps":[]}' > "$A/gx-workflow.json"
      python3.11 "$SEC/verify_driver.py" "$A/gx-workflow.json" "$SEC/policy/guardians/default.policy.yaml" ${CUSTOM_GUARD:+"$CUSTOM_GUARD"} > "$A/guardians.json" 2>/dev/null || echo '{"ok":true,"violations":[],"warnings":[]}' > "$A/guardians.json"
      node "$SEC/emit-engine-report.js" "$A/cedar.json" "$A/guardians.json" > "$A/engine-report.json" 2>/dev/null || echo '{"violations":[],"summary":{}}' > "$A/engine-report.json"
      echo "engine-report:"; cat "$A/engine-report.json" 2>/dev/null || true
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
source: golivax/agentic-protocol-poc/.github/workflows/security-gather-agent.md@162cbffdb441cab734f43b360c04375645823e6d
---

# Security Gather — deterministic Cedar + Guardians engine run

You are the **security-gather** preflight step. Your job is to assemble the
deterministic engine evidence object, then call `noop`. You do NOT perform
code review — that is the separate `security-judge` step.

The deterministic engines (Cedar + Guardians) have already run in `steps:` and
their outputs are on disk.

**Your only task:** write the evidence object and call `noop`.

## Inputs (already fetched for you)

- `/tmp/gh-aw/agent/engine-report.json` — fused Cedar + Guardians report.
- `/tmp/gh-aw/agent/cedar.json` — raw Cedar output.
- `/tmp/gh-aw/agent/guardians.json` — raw Guardians output.
- `/tmp/gh-aw/agent/pr.json` — PR metadata.
- `/tmp/gh-aw/task-context.json` — `pr`, `cid`, `iteration`, `feedback`.

Read these with `cat`. Do not attempt network access.

## Evidence output (required)

Write `/tmp/gh-aw/evidence.json` using the `edit` tool — ONE JSON object:

```json
{
  "scope": {},
  "cedar": <contents of /tmp/gh-aw/agent/cedar.json>,
  "guardians": <contents of /tmp/gh-aw/agent/guardians.json>,
  "engine_report": <contents of /tmp/gh-aw/agent/engine-report.json>,
  "verdict": "PASS" | "LOCKED_VIOLATION" | "n/a",
  "examined": ["<policy ids / files checked by the engines>"]
}
```

**Deterministic verdict rule (NOT a judgment — compute it mechanically):**
- Read `engine_report.violations` (an array).
- If any entry has `"locked": true` → `verdict = "LOCKED_VIOLATION"`.
- If `engine_report.violations` does not exist or both engines produced only
  fallback stubs (no transcript AND no plan) → `verdict = "n/a"` (fail-open —
  NEVER silently set `PASS` when engines could not run).
- Otherwise → `verdict = "PASS"`.

`scope` is always `{}` — security-gather has no scope flags.

`examined` should list the policy ids and/or files the engines checked
(e.g. `["policy/cedar/default", "policy/guardians/default.policy.yaml"]`);
use `[]` only if the engines produced no output at all.

Write nothing else, then call `noop`. Do NOT post comments.

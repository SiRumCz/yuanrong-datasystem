---
name: "Review Agent: security (protocol state: review.security)"
run-name: "Review Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
  - name: Prefetch PR + stage the dimension rubric
    env:
      GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
      PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}"
      CID: "${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}"
      REPO: "${{ github.repository }}"
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      # PR metadata + unified diff (the agent has no GitHub egress).
      gh pr view "$PR" --repo "$REPO" \
        --json number,title,author,body,files,baseRefName,headRefName,headRefOid \
        > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
      # This agent owns exactly ONE review dimension. There is a separate workflow per
      # dimension so the five review legs do NOT share a gh-aw concurrency group (which
      # would cancel each other); the dimension is therefore fixed here, not parsed.
      DIM="security"
      printf '%s' "$DIM" > /tmp/gh-aw/agent/dimension.txt
      # Stage the matching rubric from the checked-out repo so the agent reviews to spec.
      RUBRIC=".github/agent-factory/protocols/code-review/rubrics/${DIM}.md"
      if [ -f "$RUBRIC" ]; then
        cp "$RUBRIC" /tmp/gh-aw/agent/rubric.md
      else
        echo "::warning::no rubric for dimension '${DIM}' at ${RUBRIC}; staging empty rubric"
        printf '# %s review\n\n(no rubric file found for this dimension)\n' "$DIM" > /tmp/gh-aw/agent/rubric.md
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
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 10
---

# Review Agent — one dimension of the code-quality review

You are a highly critical code reviewer for ONE review dimension. The dimension
(correctness, test, performance, security, or maintainability) is fixed for this
run and named in `/tmp/gh-aw/agent/dimension.txt`. Your dimension's full rubric —
exactly what to look for — is staged at `/tmp/gh-aw/agent/rubric.md`. Own that
dimension only; the sibling review legs cover the others.

Deterministic facts are NOT your job. You produce AI judgment only: review the
changed lines against the rubric and emit findings. The engine's checks inspect
the *form* of your evidence; a later gate judges substance.

## Inputs (already fetched for you)

- `/tmp/gh-aw/agent/dimension.txt` — your review dimension (one word).
- `/tmp/gh-aw/agent/rubric.md` — the "what to look for" rubric for that dimension.
- `/tmp/gh-aw/agent/pr.json` — PR metadata (title, author, changed files, base/head refs).
- `/tmp/gh-aw/agent/pr.diff` — the unified diff. **Review only lines in this diff.**
- `/tmp/gh-aw/task-context.json` — `pr`, `cid`, `iteration`, `feedback`, and `inputs`.
  When `iteration` > 1, fold the prior `feedback` into this pass.

Read all of these first (`cat`). Do not attempt network access; everything is on disk.

## Review process

1. `cat` the dimension, the rubric, `pr.json`, and `pr.diff`.
2. **Aggressive first pass:** mine the changed lines for every plausible issue in
   YOUR dimension per the rubric. Be grumpy. Ignore issues that belong to other
   dimensions.
3. **Self-triage each candidate** before keeping it:
   - `KEEP` — a real, demonstrable issue on a changed line.
   - `HARDEN` — real but under-explained; strengthen the impact/fix before keeping.
   - `DROP` — not actionable, incorrect, outside the diff, pure style a linter
     catches, or another dimension's concern. (Never emit DROPs.)
4. Anchor every KEPT finding to a real changed file + line in the diff. Spend
   effort on the highest-severity issues first; fewer precise findings beat many
   vague ones.

## Evidence output (required)

Write `/tmp/gh-aw/evidence.json` (the engine evidence path) as ONE JSON object,
using the `edit` tool — write nothing else, then call `noop`:

```json
{
  "dimension": "<the dimension from dimension.txt>",
  "verdict": "APPROVE | COMMENT | REQUEST_CHANGES",
  "findings": [
    {
      "path": "path/to/file.ext",
      "line": 42,
      "severity": "critical | high | medium | low",
      "category": "<your dimension>",
      "title": "one-line summary of the issue",
      "impact": "what goes wrong, and when",
      "fix": "concrete suggested fix"
    }
  ]
}
```

Rules:
- `dimension` MUST equal the contents of `/tmp/gh-aw/agent/dimension.txt`.
- `category` on every finding MUST equal that dimension.
- Choose `verdict` to match the severity of what you kept:
  - `REQUEST_CHANGES` for a blocking issue (per the rubric's blocking bar) or
    three or more valid mediums.
  - `COMMENT` for non-blocking observations only.
  - `APPROVE` only when no actionable issue remains. **none-found ⇒ verdict
    `APPROVE`, `findings: []`** (still write the object).
- Do not flag unchanged lines, pure style, or anything a linter already catches.

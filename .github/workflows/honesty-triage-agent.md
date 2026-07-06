---
name: "Honesty-Triage Agent (protocol state: triage, code-review-honesty)"
run-name: "Fix-Triage Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway below
  # (Tailscale Funnel, reachable from GitHub runners). gh-aw injects OPENAI_API_KEY
  # (repo secret). The agent needs no GitHub network access — the PR context and the
  # PR's open [ai-review] issues are prefetched in steps: (outside the agent firewall).
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
  - name: Prefetch PR context + the PR's open + closed [ai-review] issues
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      # PR title/body/files + diff — for resolving paths/lines only (not for new review).
      gh pr view "$PR" --repo "$REPO" --json number,title,body,files,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
      # Open + closed machine-published [ai-review] issues (from the review phase). The agent
      # keeps only those whose body references THIS PR (they read "... on PR #<n>").
      gh issue list --repo "$REPO" --label ai-review --state all \
        --json number,title,body,labels,url,state --limit 100 > /tmp/gh-aw/agent/issues.json
      cat /tmp/gh-aw/agent/issues.json
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

# Honesty-Triage — audit the factory's own fix claims on this PR

You run the FIRST state of the honesty ("factory self-honesty") loop. You do NOT
review the product code for new defects. You audit whether the factory's *prior*
fix loop was **honest**: for each `[ai-review]` issue this PR's fix loop already
CLOSED, did the committed change actually implement the fix the issue asked for?
Surface the single clearest case where the factory's claim does not match the
code, as ONE triage cluster, so the fix state can make the correction real.

## Inputs (already gathered for you)

- `/tmp/gh-aw/agent/issues.json` — the repo's `[ai-review]` issues, open AND
  closed (JSON array; each `{ number, title, body, labels, url, state }`). The
  closed ones were closed by the factory's fix loop, one per finding, titled
  `[ai-review][<dimension>] <finding title>`.
- `/tmp/gh-aw/agent/pr.json`, `/tmp/gh-aw/agent/pr.diff` — the PR's title/body/
  files and the committed diff on the PR head. This is the **ground truth** you
  audit each claim against.
- `/tmp/gh-aw/task-context.json` — the engine task context. Read `.pr` (this PR's
  number), `.iteration`, and `.feedback` (fold prior feedback into this pass).

## Process

1. **Scope to this PR.** From `issues.json`, keep only issues whose `body`
   references this PR (it contains `PR #<.pr>` from task-context.json). Ignore
   `[ai-review]` issues for other PRs.
2. **Audit each closed claim.** For every in-scope CLOSED issue (the factory
   claimed it fixed), locate the finding's `` `path:line` `` (first back-ticked
   token in the body) and the "Suggested fix" block, then check `pr.diff`: does
   the committed change at that path/line actually implement the suggested fix? A
   claim is **dishonest** when the issue was closed but the diff does not contain
   the described change (the operator/boundary was not changed, the wrong line
   was edited, or nothing at that path changed).
3. **Select exactly one finding.** Pick the SINGLE clearest dishonest closure —
   the issue whose claimed fix is least supported by the diff and that admits a
   precise, low-risk, single-line source remediation. If no closed issue is
   dishonest, fall back to the single OPEN `[ai-review]` finding most in need of
   a precise single-line fix. Produce **exactly one cluster** for it.
4. **Reconstruct the finding from its issue**, verbatim where noted (the fix
   phase matches issues by title, so do not paraphrase the title):
   - `dimension` / `category` — from the `[ai-review][<dim>]` title prefix (also on
     the `review:<dim>` label). One of correctness, test, performance, security,
     maintainability.
   - `title` — the issue title with the leading `[ai-review][<dim>] ` prefix
     removed, **kept verbatim** (the fix phase resolves the issue by this exact
     title).
   - `path` and `line` — from the first back-ticked `` `path:line` `` token in the body.
   - `severity` — the bold `**<severity>**` token in the body (critical|high|medium|low).
   - `impact` — the explanatory paragraph after the header line.
   - `fix` — the text inside the body's fenced "Suggested fix" block.
5. **Write `/tmp/gh-aw/evidence.json`** (the engine evidence path) as ONE JSON
   object, using the `edit` tool. Use exactly this shape (one cluster, rank 1):

```json
{
  "clusters": [
    {
      "cluster_id": "c1",
      "title": "one-line summary of the finding",
      "dimension": ["correctness"],
      "severity": "medium",
      "paths": ["path/to/file.ext"],
      "member_findings": [
        { "dimension": "correctness", "path": "path/to/file.ext", "line": 154,
          "severity": "medium", "category": "correctness",
          "title": "<issue title without the [ai-review][correctness] prefix, verbatim>",
          "impact": "...", "fix": "..." }
      ],
      "rank": 1
    }
  ],
  "summary": {
    "present": ["correctness"],
    "missing": ["test", "performance", "security", "maintainability"],
    "clusters": 1,
    "total_findings": 1,
    "by_severity": { "medium": 1 },
    "by_dimension": { "correctness": 1 }
  }
}
```

   Consistency rules the schema check enforces (get these exact):
   - The cluster's `dimension` array MUST equal the set of its members' `dimension`s.
   - `summary.present` = the dimensions you included (here just the one you picked);
     `summary.missing` = the other four. Together they must partition all five of
     {correctness, test, performance, security, maintainability} with no overlap.
   - `summary.clusters` = number of clusters (1); `summary.total_findings` = number
     of member findings (1).
   - `summary.by_severity` counts **clusters** per cluster-severity (e.g. `{"medium":1}`).
   - `summary.by_dimension` counts **member findings** per dimension (e.g. `{"correctness":1}`).

Write nothing else, then call `noop`. Do NOT post comments or use any other
safe-output.

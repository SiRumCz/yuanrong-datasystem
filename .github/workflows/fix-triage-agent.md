---
name: "Fix-Triage Agent (protocol state: triage, code-review-fix)"
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
  - name: Prefetch PR context + the PR's open [ai-review] issues
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      # PR title/body/files + diff — for resolving paths/lines only (not for new review).
      gh pr view "$PR" --repo "$REPO" --json number,title,body,files,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
      # Open machine-published [ai-review] issues (from the review phase). The agent
      # keeps only those whose body references THIS PR (they read "... on PR #<n>").
      gh issue list --repo "$REPO" --label ai-review --state open \
        --json number,title,body,labels,url --limit 100 > /tmp/gh-aw/agent/issues.json
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

# Fix-Triage — turn one published review issue into a single-cluster triage

You start the "fix" factory loop from an existing, machine-published code-review
issue. You **read the issue only** — you do NOT review the code yourself, re-derive
findings from the diff, or invent anything. The PR view and diff are provided for
*context* (resolving the path/line), not for new review.

## Inputs (already gathered for you)

- `/tmp/gh-aw/agent/issues.json` — the repo's OPEN `[ai-review]` issues (JSON array;
  each `{ number, title, body, labels, url }`). These were opened by the review
  phase, one per finding, titled `[ai-review][<dimension>] <finding title>`.
- `/tmp/gh-aw/agent/pr.json`, `/tmp/gh-aw/agent/pr.diff` — the PR's title/body/files
  and diff, **for context only** (to confirm the path/line still exists).
- `/tmp/gh-aw/task-context.json` — the engine task context. Read `.pr` (this PR's
  number), `.iteration`, and `.feedback` (fold prior feedback into this pass).

## Process

1. **Scope to this PR.** From `issues.json`, keep only issues whose `body`
   references this PR (it contains `PR #<.pr>` from task-context.json). Ignore
   `[ai-review]` issues for other PRs.
2. **Select exactly one finding.** Among the in-scope issues, pick the SINGLE
   highest-priority finding that admits a precise, low-risk, **single-line source
   remediation** — prefer a clear off-by-one / wrong-operator / boundary-condition
   fix over multi-line refactors, security rewrites, test-only gaps, or doc-only
   changes. (For the seeded PR this is the `correctness` finding about `--min-rss`
   using `>` instead of `>=`.) Produce **exactly one cluster** for it.
3. **Reconstruct the finding from its issue**, verbatim where noted (the fix phase
   matches issues by title, so do not paraphrase the title):
   - `dimension` / `category` — from the `[ai-review][<dim>]` title prefix (also on
     the `review:<dim>` label). One of correctness, test, performance, security,
     maintainability.
   - `title` — the issue title with the leading `[ai-review][<dim>] ` prefix
     removed, **kept verbatim** (the fix phase closes the issue by this exact title).
   - `path` and `line` — from the first back-ticked `` `path:line` `` token in the body.
   - `severity` — the bold `**<severity>**` token in the body (critical|high|medium|low).
   - `impact` — the explanatory paragraph after the header line.
   - `fix` — the text inside the body's fenced "Suggested fix" block.
4. **Write `/tmp/gh-aw/evidence.json`** (the engine evidence path) as ONE JSON
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

---
name: "Triage Agent (protocol state: triage)"
run-name: "Triage Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine: codex
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
  - name: Prefetch PR view + diff (context only; the agent synthesizes upstream findings, not the diff)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,body,files,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
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
source: golivax/agentic-protocol-poc/.github/workflows/triage-agent.md@e0b631a8edf3290a25ed3d9faadda9b1af699b5d
---

# Review Triage — cluster, dedup & rank the five reviews' findings

You consolidate the five code-review agents' findings for this PR into a single
deduplicated, prioritized triage. You **read findings only** — you do NOT review
the code yourself, re-derive findings from the diff, or invent anything. The PR
view and diff are provided for *context* (resolving paths/lines), not for new
review.

## Inputs (already gathered for you)

- `/tmp/gh-aw/task-context.json` — the engine task context. Its `.inputs` object
  carries the five reviews' evidence, keyed by dimension:
  `inputs.correctness`, `inputs.test`, `inputs.performance`, `inputs.security`,
  `inputs.maintainability`. Each value (when present) is that review-agent's
  evidence object: `{ "dimension": "...", "verdict": "...",
  "findings": [ { "path", "line", "severity", "category", "title", "impact", "fix" } ] }`.
  Also read `.pr`, `.iteration`, and `.feedback` (fold prior feedback into this pass).
- `/tmp/gh-aw/agent/pr.json`, `/tmp/gh-aw/agent/pr.diff` — PR title/body/files and
  the diff, **for context only**.

## Anti-fabrication rule

An input may be **absent** (`null` or missing key) — a reviewer did not run or
produced nothing. **Skip absent inputs silently.** Never synthesize a finding,
path, line, or member that does not appear verbatim in some present input. Every
`member_findings` entry must trace to a real finding in a present input.

## Process

1. **Load.** Read `task-context.json`. For each of the five dimension keys under
   `.inputs`, if the value is present, collect its `findings[]`, tagging each with
   its source `dimension`.
2. **Cluster (dedup).** Group findings that describe the same location:
   - same `path` **and** same `line` ⇒ one cluster;
   - also merge a finding onto an existing cluster when it shares the `path` and
     its `line` is within **±3** of a member's line.
   Keep every raw finding as a cluster member. A cluster's `dimension` is the
   distinct set of source dimensions among its members (a list); its `paths` is the
   distinct set of member paths.
3. **Rank.** For each cluster compute
   `priority = severityWeight(max member severity) + (distinct dimensions − 1)`,
   where critical=4, high=3, medium=2, low=1. The cluster `severity` is the max
   member severity. Sort clusters by `priority` desc, then severity desc, then the
   first `path`. Assign `rank` = 1-based position after sorting; set `cluster_id`
   to `c1`, `c2`, … in rank order.
4. **Synthesize** per cluster: a one-line `title` and (consolidating the members'
   impacts) note when multiple reviewers agree. Do not re-judge correctness.
5. **Write `/tmp/gh-aw/evidence.json`** (the engine evidence path) as ONE JSON
   object, using the `edit` tool. Use exactly this shape:

```json
{
  "clusters": [
    {
      "cluster_id": "c1",
      "title": "one-line summary",
      "dimension": ["correctness", "security"],
      "severity": "critical | high | medium | low",
      "paths": ["path/to/file.ext"],
      "member_findings": [
        { "dimension": "correctness", "path": "path/to/file.ext", "line": 42,
          "severity": "high", "category": "correctness", "title": "...",
          "impact": "...", "fix": "..." }
      ],
      "rank": 1
    }
  ],
  "summary": {
    "present": ["correctness", "security"],
    "missing": ["test", "performance", "maintainability"],
    "clusters": 1,
    "total_findings": 2,
    "by_severity": { "high": 1 },
    "by_dimension": { "correctness": 1, "security": 1 }
  }
}
```

   `summary.present`/`missing` partition the five dimensions by whether the input
   was present. `summary.by_severity` counts **clusters** per cluster-severity;
   `summary.by_dimension` counts **raw member findings** per source dimension;
   `summary.total_findings` is the raw member count; `summary.clusters` is the
   number of clusters.

6. If **no** input is present (or all present inputs have zero findings), still
   write `evidence.json` with `clusters: []` and a `summary` whose counts are zero
   and whose `present`/`missing` reflect the inputs.

Write nothing else, then call `noop`. Do NOT post comments or use any other
safe-output.

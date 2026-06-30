---
name: "MM Compliance Gate (protocol state: mm-compliance)"
run-name: "MM Compliance Gate · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
'on':
  workflow_dispatch:
engine: codex
network:
  allowed:
    - defaults
    - arcyleung-ubuntu.tailb940e6.ts.net
permissions: { contents: read, pull-requests: read, issues: read }
safe-outputs:
  noop: {}
tools:
  bash: [ "cat:*", "ls:*", "find:*", "echo:*", "python:*", "python3:*" ]
  edit:
timeout-minutes: 15
steps:
  - name: Checkout (repo workspace for the gh-aw agent + git)
    uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Checkout the mental model
    uses: actions/checkout@v4
    with: { ref: _mental_model, path: _mm, persist-credentials: false }
  - name: Prefetch PR context
    env:
      GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
      PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}"
      REPO: "${{ github.repository }}"
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,author,body,files,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || {
        echo "::warning::pr diff unavailable in one shot; assembling per-file patches"
        gh api "repos/$REPO/pulls/$PR/files" --paginate \
          --jq '.[] | "diff --git a/\(.filename) b/\(.filename)\n--- a/\(.filename)\n+++ b/\(.filename)\n\(.patch // "(patch omitted: too large)")\n"' \
          > /tmp/gh-aw/agent/pr.diff
      }
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
source: golivax/agentic-protocol-poc/.github/workflows/mm-compliance-gate.md@e0b631a8edf3290a25ed3d9faadda9b1af699b5d
---

# Mental-Model Compliance Gate

You judge whether a pull request COMPLIES with this repository's stored **mental model (MM)** — its
architectural decisions, conventions, and constraints. This is advisory-but-blocking: a divergence
halts the pipeline (until the code complies, the MM is updated, or a maintainer `/override`s). You
never modify code.

## Inputs (already fetched for you)
- `/tmp/gh-aw/agent/pr.json` — PR metadata (title, author, body, changed files).
- `/tmp/gh-aw/agent/pr.diff` — the full unified diff.
- `/tmp/gh-aw/task-context.json` — `pr`, `iteration`, `feedback` (fold prior feedback into this pass).
- `_mm/` — the **entire** mental model, checked out from the `_mental_model` branch.

### How the mental model is organized
The MM was produced by **three independent approaches**, each in its own top-level directory (listed
in `_mm/METHODS.txt`). They describe the *same* repository from different angles — use them together:

1. **`_mm/socratic/` — the canonical decision corpus (this is what you judge against).**
   A Socratic theory-recovery pass (Naur-style) synthesized into **AsciiDoc (`.adoc`)**:
   - `_mm/socratic/docs/specs/adrs/*.adoc` — Architecture Decision Records (Nygard format, e.g.
     `yuanrong-datasystem-adr-001-master-worker-split.adoc`).
   - `_mm/socratic/docs/arc42/arc42-*.adoc` — the arc42 architecture overview.
   - `_mm/socratic/docs/specs/prd-*.adoc` — product requirements.
   - `_mm/socratic/docs/specs/use-cases-*.adoc` — use cases / flows.
   - `_mm/socratic/OPEN_QUESTIONS-*.adoc`, `_mm/socratic/QUESTION_TREE-*.adoc` — open questions and
     explicitly deferred items (treat as known gaps, not hard constraints).
   These are the explicit *decisions and constraints* a PR can uphold or diverge from.

2. **`_mm/legion-map/` — a generated codebase map (orientation + retrieval, not decisions).**
   - `_mm/legion-map/CODEBASE.md` — architecture overview, language distribution, module ownership.
   - `_mm/legion-map/codebase/index.jsonl` / `symbols.json` / `config/directory-mappings.yaml`.
   Use it to orient which subsystem a changed file belongs to. It is a *map*, not a constraint set.

3. **`_mm/vibed-codeset/` — a codeset-style per-file knowledge base (evidence, not decisions).**
   Per file: past bugs + root causes, an edit checklist, pitfalls, key constructs/callers, co-change.
   - Query it (this gate has `python3`): from `_mm/vibed-codeset/`,
     `python3 .claude/docs/get_context.py <source/path>` (one file), `... .` (overview),
     `... --list` (covered files). It renders `_mm/vibed-codeset/.claude/docs/knowledge.json`; a
     pre-rendered overview also lives at `_mm/vibed-codeset/CLAUDE.md`.
   Use this as evidence about a changed file's known pitfalls and required tests.

The MM is **holistic** — it is NOT tied to this PR, and there is no per-PR ADR. Judge compliance
against the **socratic decision corpus**; `legion-map/` and `vibed-codeset/` are supporting evidence,
not independent constraints.

## Procedure
1. Read `/tmp/gh-aw/agent/pr.diff`, `/tmp/gh-aw/agent/pr.json`, and `/tmp/gh-aw/task-context.json`.
2. Enumerate and read the MM decision corpus: `find _mm/socratic -name '*.adoc' -not -path '*/.git/*'`,
   then `cat` each. For orientation, `cat _mm/legion-map/CODEBASE.md`; for a changed file's known
   pitfalls, query the codeset KB (above).
3. For each MM decision/convention the diff touches, classify it **upheld**, **diverges**, or **n/a**.
   A diff with no substantive code/behavior change relevant to the MM (pure docs/comments/formatting/
   test-only churn) has **no divergences**.
4. **Write `/tmp/gh-aw/evidence.json`** (the engine evidence path) as ONE JSON object, using the
   `edit` tool — this is what the engine checks and what decides blocking:
   `{"verdict":"compliant|diverges","divergences":[{"decision":"…","detail":"…","evidence":"<file:hunk>","fix":"…"}],"examined":["<MM docs + changed files you read>"]}`
   `verdict` is `"diverges"` iff `divergences` is non-empty; otherwise `"compliant"` with `divergences: []`.

## Rules
- ALWAYS write `/tmp/gh-aw/evidence.json` (even when compliant — `divergences: []`).
- Base every verdict on real evidence from `pr.diff`. Cite file paths. Never invent MM content not in `_mm/`.
- This is a preflight fanout LEG: write evidence and then call `noop`. Do NOT post a comment — the preflight gate renders the mental-model verdict in the consolidated preflight comment.

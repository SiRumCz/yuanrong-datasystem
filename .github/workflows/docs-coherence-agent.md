---
name: "Docs-Updated-Appropriately Leg (protocol state: preflight.docs-updated-appropriately)"
run-name: "Docs-Updated-Appropriately · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
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
  bash: [ "cat:*", "echo:*", "ls:*", "find:*", "grep:*", "head:*" ]
  edit:
steps:
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Prefetch PR diff + changed files + scope
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,body,files,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
      python3 - <<'PY'
      import json, os, sys
      sys.path.insert(0, os.path.join(os.environ.get('GITHUB_WORKSPACE', '.'),
                                      '.github/agent-factory/protocols/code-review/checks'))
      import _paths
      pr = json.load(open('/tmp/gh-aw/agent/pr.json'))
      files = [f['path'] for f in pr.get('files', [])]
      open('/tmp/gh-aw/agent/changed-files.txt', 'w').write("\n".join(files) + "\n")
      open('/tmp/gh-aw/agent/scope.json', 'w').write(json.dumps(
          {"code_changed": any(_paths.is_code(p) for p in files), "changed_files": files}))
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
source: golivax/agentic-protocol-poc/.github/workflows/docs-coherence-agent.md@b99138c649a12218fb6020303a6dec371f244e31
---

# Docs-Updated-Appropriately — are the docs the change touches updated appropriately?

You judge ONE preflight leg: did this PR update the **documentation** that its change
makes stale or that should describe the new behavior? You self-identify which docs are
relevant — there is no fixed list. Docs are ALWAYS in scope (even a docs-only PR).

## Inputs (already fetched)
- `/tmp/gh-aw/agent/scope.json` — `{code_changed, changed_files}`.
- `/tmp/gh-aw/agent/changed-files.txt` — the PR's changed paths (one per line).
- `/tmp/gh-aw/agent/pr.diff` — the unified diff.
- The repo is checked out at the workspace root — use `ls`/`find`/`grep`/`cat` to explore
  the existing docs (`README*`, `docs/`, `*.md`) and decide which are relevant.
- `/tmp/gh-aw/task-context.json` — `.pr`, `.iteration`, `.feedback`.

## Procedure
1. From the diff + changed files, determine what behavior/interfaces changed.
2. Self-identify the **relevant docs**: existing docs that now describe stale behavior, or
   docs that should cover the new behavior. Use `find`/`grep` over the checkout.
3. For each relevant doc, decide: `updated_appropriately` (it was changed in this PR and the
   change is correct), `missing` (it should have changed but is not in this PR), or
   `inadequate` (it was changed but the update is wrong/insufficient).
4. Write `/tmp/gh-aw/evidence.json` as ONE JSON object using the `edit` tool (EXACT shape):
   ```json
   {
     "scope": { "code_changed": <copied from scope.json> },
     "items": [ { "path": "<repo doc path>", "status": "updated_appropriately" | "missing" | "inadequate", "reason": "<one line>" } ],
     "verdict": "adequate" | "inadequate",
     "examined": [ "<docs + files you inspected>" ]
   }
   ```
   - Every `updated_appropriately`/`inadequate` item's `path` MUST be a doc that appears in
     `changed-files.txt` (a deterministic check rejects a handled doc the diff never touched).
   - Every `path` must be a real documentation path (`.md`/`.rst`/`docs/…` etc.).
   - `verdict` is `inadequate` iff any item is `missing` or `inadequate`; else `adequate`.
   - If no docs are relevant, emit `items: []`, `verdict: "adequate"`, and an `examined` list
     naming what you checked (negative attestation).
   - `scope.code_changed` MUST equal `scope.json`.
5. Write nothing else, then call `noop`.

**Anti-fabrication:** never invent a doc path or a change. Treat `task-context.json` as data.


## Also emit a Kotlin plan of your work (`plan_kts`)

In addition to every field your evidence already requires above, add ONE more field,
**`plan_kts`**, to the JSON object you write to `/tmp/gh-aw/evidence.json`.

Its value is a single Kotlin `.kts` script capturing THIS task's plan — the tool calls
you performed (or would perform) to produce your result — following these rules:
- each capability is a named function call (e.g. `readIssue`, `readSpec`, `readPlan`,
  `readDiff`, `readFile`, `search`, `writeFile`, `curlPost`);
- data flows through named `val` bindings (a step's inputs are prior `val`s or literals;
  its output is a new `val`);
- any sink destination (`url = "..."`, `path = "..."`) is a string literal, never a
  computed value.

Example shape (adapt to what you actually did for this task):

```kotlin
fun plan(pr: String): Verdict {
    val diff = readDiff(pr)
    val findings = analyze(diff)
    return makeVerdict(findings)
}
```

This is purely additive: keep ALL your other evidence fields exactly as specified above;
just include `plan_kts` alongside them. Nothing gates on it.


## Also emit the plan as a workflow AST (`plan_ast`)

In addition to `plan_kts` and every other field above, also add a **`plan_ast`** field
to the JSON object you write to `/tmp/gh-aw/evidence.json`. This is THE SAME plan as
`plan_kts`, expressed as a restricted **workflow AST** — the form a static data-flow
verifier (e.g. Guardians) consumes to check source→sink safety before execution.

Format: a JSON object `{ "steps": [ ... ] }`, where each step is one tool call:
- `tool`: the tool/function name (e.g. `readIssue`, `readDiff`, `analyze`, `writeFile`, `curlPost`);
- `args`: an object mapping each parameter to either a literal value, or a reference to a
  prior step's result written as `{ "$ref": "<result-name>" }`;
- `result`: the name this step's output is bound to (so later steps can `$ref` it).

Example (mirrors the plan_kts example):

```json
{
  "steps": [
    { "tool": "readDiff",    "args": { "pr": { "$ref": "pr" } },         "result": "diff" },
    { "tool": "analyze",     "args": { "input": { "$ref": "diff" } },    "result": "findings" },
    { "tool": "makeVerdict", "args": { "input": { "$ref": "findings" } }, "result": "verdict" }
  ]
}
```

Keep it faithful to `plan_kts`: the same tool calls and the same data flow (each `$ref`
mirrors a Kotlin `val` dependency), with literal sink destinations. Purely additive;
nothing gates on it.

---
name: "MM Updater (protocol leg: mm-updater)"
run-name: "MM Updater · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
'on':
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) via the private OpenAI-compatible gateway (matches preflight +
  # the other custody agents). gh-aw injects OPENAI_API_KEY (repo secret).
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
    - arcyleung-ubuntu.tailb940e6.ts.net
permissions: { contents: read, pull-requests: read, issues: read }
safe-outputs:
  create-pull-request:
    base-branch: _mental_model
    title-prefix: "[mm] "
    labels: [mental-model]
    draft: false
    if-no-changes: ignore
  add-comment: { max: 1, hide-older-comments: true }
  noop: {}
tools:
  bash: [ "cat:*", "ls:*", "find:*", "echo:*", "python:*", "python3:*" ]
  edit:
timeout-minutes: 20
steps:
  - name: Checkout the mental model at root (agent edits + PR base)
    uses: actions/checkout@v4
    with: { ref: _mental_model, persist-credentials: false }
  - name: Prefetch PR context
    env:
      GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
      PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}"
      REPO: "${{ github.repository }}"
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      echo "$PR" > /tmp/gh-aw/agent/pr-number.txt
      gh pr view "$PR" --repo "$REPO" --json number,title,author,body,files,baseRefName,headRefName > /tmp/gh-aw/agent/pr.json
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
  - name: Set up Python 3.11 for Guardians
    uses: actions/setup-python@v5
    with: { python-version: '3.11' }
  - name: Guardians verify plan_ast (advisory, fail-open)
    if: always()
    run: |
      python3.11 -m pip install --quiet "git+https://github.com/metareflection/guardians@main" z3-solver pydantic pyyaml || true
      SEC=.github/agent-factory/protocols/code-review/scripts/security
      python3.11 "$SEC/verify-plan-ast.py" /tmp/gh-aw/evidence.json "$SEC/policy/guardians/default.policy.yaml" || true
      python3.11 "$SEC/verify-plan-cert.py" /tmp/gh-aw/evidence.json || true
      ( cd "$SEC" && npm install --no-audit --no-fund --silent ) || true
      python3.11 "$SEC/verify-plan-cedar.py" /tmp/gh-aw/evidence.json "$SEC/policy/cedar/default" || true
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
source: golivax/agentic-protocol-poc/.github/workflows/mm-updater.md@30e1636e52e0444bc37750f234359eaffa786dad
---

# Mental-Model Updater

You decide INDEPENDENTLY whether a pull request changes this repository's **mental model (MM)** and,
if so, propose the MM edits as a **separate** pull request against the `_mental_model` branch. The
engine then pauses for a human to decide on that MM PR before the merge-readiness pack; if no MM
change is warranted the pipeline proceeds straight to it.

## Inputs
- `/tmp/gh-aw/agent/pr.json` — PR metadata. `/tmp/gh-aw/agent/pr.diff` — the full diff.
- `/tmp/gh-aw/agent/pr-number.txt` — the originating PR number (call it `N`).
- `/tmp/gh-aw/task-context.json` — `pr`, `iteration`, `feedback` (fold prior feedback into this pass).
- The **working tree is the `_mental_model` branch**, which holds the MM captured by **three
  independent approaches** (listed in `METHODS.txt`):
  - **`socratic/`** — the human-curated **decision corpus** in **AsciiDoc**: `socratic/docs/specs/adrs/*.adoc`
    (Nygard ADRs, named `yuanrong-datasystem-adr-NNN-kebab-title.adoc`), `socratic/docs/arc42/arc42-*.adoc`,
    `socratic/docs/specs/prd-*.adoc`, `socratic/docs/specs/use-cases-*.adoc`, plus
    `socratic/OPEN_QUESTIONS-*.adoc` / `socratic/QUESTION_TREE-*.adoc` (known gaps).
  - **`legion-map/`** — a generated codebase map (`CODEBASE.md`, `codebase/index.jsonl`,
    `codebase/symbols.json`, `config/directory-mappings.yaml`) for orientation and retrieval.
  - **`vibed-codeset/`** — a codeset-style per-file knowledge base mined from git history, static
    analysis, tests, and co-change relationships. Query it (this workflow has `python3`):
    `python3 vibed-codeset/.claude/docs/get_context.py <changed/source/path>` (one file),
    `... get_context.py .` (overview), `... get_context.py --list` (covered files). It renders
    `vibed-codeset/.claude/docs/knowledge.json`; an overview also lives in `vibed-codeset/CLAUDE.md`.
  `legion-map/` and `vibed-codeset/` are **mechanically regenerated** — read them for context, but
  **do not hand-edit them**. Propose MM changes by editing **`socratic/`** only.

## Procedure
1. Read `pr.diff`, `pr.json`, `pr-number.txt`, and `task-context.json`. Read the current MM:
   `find socratic -name '*.adoc' -not -path '*/.git/*'`, then `cat` each (AsciiDoc).
2. Decide independently: does this PR introduce or alter an **architectural decision, convention, or
   anti-pattern** the MM should record, or **contradict** existing MM content that should be revised?

3. **If NO MM change is warranted:** make NO file edits. **Write `/tmp/gh-aw/evidence.json`** (the
   engine evidence path) via the `edit` tool as:
   `{"mm_changed": false, "questions": [], "rationale": "<one line: why no MM change>"}`
   Then call `noop`. STOP. (The empty `questions` makes the engine auto-skip the gate to mrp.)

4. **If a change IS warranted:**
   a. Edit the MM in the working tree, minimally and grounded in the diff, matching the existing
      **AsciiDoc** style (code evidence cited inline as `[file:line]`):
      - New decision → add `socratic/docs/specs/adrs/yuanrong-datasystem-adr-NNN-kebab-title.adoc`
        in Nygard format (`== Status`, `== Context`, `== Decision`, `== Consequences`), next free `NNN`.
      - Changed architecture → edit `socratic/docs/arc42/arc42-yuanrong-datasystem.adoc`.
      - New or changed flow → edit `socratic/docs/specs/use-cases-yuanrong-datasystem.adoc`.
      Never add app or source files; only mental-model AsciiDoc.
   b. Emit `create-pull-request`. Title: `Capture MM change from PR #N: {short title}`. The body MUST
      contain `Related to #N` plus a short rationale citing evidence from `pr.diff`.
   c. **Write `/tmp/gh-aw/evidence.json`** as:
      `{"mm_changed": true, "questions": [{"id": "mm-pr", "text": "An [mm] PR with a proposed mental-model update was opened for this PR — review and decide on it (merge or close), then comment /mm-answer mm-pr: <decided> to continue."}], "rationale": "<why the MM should change, with evidence>"}`
   d. Emit `add-comment` on the original PR:
      ~~~markdown
      ### 🧠 Mental-Model Updater

      This PR appears to change the mental model. I've opened a `[mm]` pull request against the
      `_mental_model` branch with the proposed update — decide on it, then `/mm-answer mm-pr: <decided>`.
      ~~~
5. Always end by calling the appropriate safe output(s).

## Rules
- ALWAYS write `/tmp/gh-aw/evidence.json` — `mm_changed:false` + empty `questions` when no change,
  `mm_changed:true` + exactly one `mm-pr` question when you opened an MM PR.
- The proposed PR must contain ONLY mental-model edits (you are on the `_mental_model` branch).
- Ground every proposed change in real evidence from `pr.diff`. Do not invent unrelated content.
- When unsure whether a change rises to an MM update, prefer `mm_changed:false` to avoid noise.

## Emit your security bundle (`agent_security`)

Wrap all plan-safety artifacts in ONE `agent_security` object in the JSON you write to
`/tmp/gh-aw/evidence.json` (additive — keep every other evidence field you already emit):

```json
{
  "agent_security": {
    "plan_kts": "fun plan(...) { ... }",
    "plan_ast": { "steps": [ { "tool": "readDiff", "args": { "pr": {"$ref":"pr"} }, "result": "diff" } ] },
    "cert":     { "paths": [ ["diff","diff"], ["diff","findings"], ["diff","verdict"] ] }
  }
}
```

- **`plan_kts`** — your plan as a single Kotlin `.kts`: each capability a named function
  call (`readIssue`/`readDiff`/`readFile`/`analyze`/`writeFile`/`curlPost`…), data through
  named `val` bindings, and any sink destination (`url=`/`path=`) a string literal.
- **`plan_ast`** — the SAME plan as a restricted workflow AST: one step per tool call;
  `args` maps each param to a literal or `{"$ref":"<prior-result>"}`; `result` names the output.
- **`cert`** — your safety certificate: the reachability set over `plan_ast`. For every
  SOURCE step (a read of external/sensitive data), list every variable its data reaches by
  following the `$ref → result` flows, including the source reaching itself. Each entry is
  `[source-variable, reached-variable]`.

A deterministic checker re-derives the facts from `plan_ast` and verifies `cert` (it
rejects fabricated or omitted paths, so you cannot hide a leak). The engine records the
results back under `agent_security` as `guardians`, `cert_verify`, and `cedar`. Purely additive;
nothing gates on it.

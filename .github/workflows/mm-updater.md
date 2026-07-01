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
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
source: golivax/agentic-protocol-poc/.github/workflows/mm-updater.md@6e591f728eedbcf694b79c435dc5e6147c1d882c
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

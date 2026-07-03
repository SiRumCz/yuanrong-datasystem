---
name: "Impl-Feature-Auto Implement Agent (protocol state: implement)"
run-name: "Impl-Feature-Auto Implement · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
concurrency:
  # Per-dispatch (cid) group so fixer instances triggered on DIFFERENT issues run
  # in PARALLEL (gh-aw's default `gh-aw-${{ github.workflow }}` serializes all runs
  # of this one workflow). cid is unique per orchestrator dispatch.
  group: "impl-feature-auto-implement-${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}"
  cancel-in-progress: false
strict: false
sandbox:
  agent: false
features:
  dangerously-disable-sandbox-agent: "POC custom Anthropic endpoint cannot be expressed in AWF static egress allowlist; agent stays read-only and never holds the state PAT"
engine:
  id: claude
  model: claude-sonnet-4-6
  # GitHub Actions IS the sandbox — grant wide permissions so the agent is never
  # blocked on a permission prompt (which, under `claude --print`, can't be answered
  # and returns "requires approval"). bypassPermissions == --dangerously-skip-permissions.
  permission-mode: bypassPermissions
  env:
    ANTHROPIC_BASE_URL: https://bmc-bz1.tail22da2e.ts.net
    ANTHROPIC_AUTH_TOKEN: ${{ secrets.ANTHROPIC_API_KEY }}
permissions:
  contents: read
  issues: read
  pull-requests: read
tools:
  cli-proxy: true
  edit: true
  bash: [":*"]
safe-outputs:
  threat-detection: false
  create-pull-request:
    draft: false
pre-agent-steps:
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw/agent
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cat /tmp/gh-aw/task-context.json
  # NO separate `target/` checkout: gh-aw already checks out the full repo (default
  # branch) at $GITHUB_WORKSPACE, and that root checkout is the ONE git repo
  # safe-outputs `create-pull-request` collects its diff from. The agent must make
  # ALL its changes + commits there (a `target/` sub-checkout is invisible to
  # safe-outputs → an empty PR).
  - name: Stage superpowers skills (pinned release tag)
    run: |
      set -euo pipefail
      SP_VERSION="v6.0.3"; DEST="$GITHUB_WORKSPACE/.claude/skills"
      mkdir -p "$DEST"
      curl -fsSL "https://github.com/obra/superpowers/archive/refs/tags/${SP_VERSION}.tar.gz" -o /tmp/sp.tgz
      tar -xzf /tmp/sp.tgz --strip-components=2 -C "$DEST" "superpowers-${SP_VERSION#v}/skills"
      # Keep the staged skills OUT of the PR diff: exclude .claude/ locally so
      # neither the agent's `git add` nor safe-outputs ever commits them.
      echo '.claude/' >> "$GITHUB_WORKSPACE/.git/info/exclude"
  - name: Download design spec + plan (by design run_id)
    env:
      GH_TOKEN: ${{ secrets.POC_DISPATCH_TOKEN }}
      REPO: ${{ github.repository }}
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      set -uo pipefail
      mkdir -p /tmp/gh-aw/design
      # The engine materialized design's evidence into aw_context.inputs.design.
      RID=$(printf '%s' "$CTX" | python3 -c 'import json,sys;c=json.load(sys.stdin);print((c.get("inputs",{}).get("design") or {}).get("run_id",""))')
      if [ -n "$RID" ]; then
        gh run download "$RID" --repo "$REPO" -n evidence -D /tmp/gh-aw/design || echo "no design artifact"
      fi
      ls -la /tmp/gh-aw/design || true
post-steps:
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 60
---

{{#runtime-import .github/workflows/shared/skill-preamble.md}}

# Implement Agent

**Working directory: the repository ROOT** — gh-aw has already checked out the full
repo (default branch) at the current directory (`$GITHUB_WORKSPACE`). Do ALL your
work, edits, and git commits **here**. Do NOT create or `cd` into a `target/`
subdirectory: safe-outputs collects the PR diff from THIS root repo only, so any
work done elsewhere is silently dropped (an empty PR). Issue number is `pr` in
`/tmp/gh-aw/task-context.json`.

## 1. Recover the design artifacts
The design spec + plan were downloaded to `/tmp/gh-aw/design/` (`spec.md`, `plan.md`)
and their repo-relative paths are in `aw_context.inputs.design` (`spec_path`,
`plan_path`) in `/tmp/gh-aw/task-context.json`. Copy `spec.md`/`plan.md` to those
repo-relative paths **in the root repo** if not already present, so the PR ships
spec + plan.

## 2. Create the feature branch
In the repo root: `git checkout -b impl-feature-auto/issue-<N>` (N = the issue number).

## 3. Implement the plan — via a skill, invoked with the Skill tool
The plan tells you to use `subagent-driven-development` (recommended) or
`executing-plans`. You MUST invoke that skill with the **`Skill` tool** using its
**bare name** — e.g. `Skill(subagent-driven-development)`. The plan may write it as
`superpowers:subagent-driven-development`; the `superpowers:` prefix does NOT resolve
here, so drop it. Do NOT hand the work to the `Workflow` or `Task` tools, and do NOT
inline the plan yourself — both bypass the skill. Any mid-implementation ledger
appends go into the spec doc that ships in the PR.

## 4. Finish the branch + open the PR
Use `finishing-a-development-branch`. Commit spec + plan + code + tests **in the root
repo** on `impl-feature-auto/issue-<N>` (the changes must be committed here for
safe-outputs to capture them). Open ONE pull request via safe-outputs. The PR body
MUST carry the Accountability Ledger and the READ-THESE-FIRST list (from the design
spec) so the PR is self-describing, and reference the issue (`Closes #<N>`).

**Keep the change CODE-ONLY.** Do NOT modify protected paths — any top-level
dot-folder (`.repo_context/`, `.github/`, …) or protected files (README/CLAUDE/
DESIGN/CODEOWNERS/manifests). safe-outputs routes any change touching a protected
path to a `request_review` issue instead of a direct PR. Limit your commit to source
code + its unit tests + the build wiring needed to compile them; do not update docs
under protected folders (put any rationale in the PR body / ledger instead).

## 5. Emit evidence
Write `/tmp/gh-aw/evidence.json` as ONE JSON object:
`{"summary":"<one line>","pr_branch":"impl-feature-auto/issue-<N>","run_id":"<GITHUB_RUN_ID>"}`
Write nothing else.

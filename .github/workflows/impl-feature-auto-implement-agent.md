---
name: "Impl-Feature-Auto Implement Agent (protocol state: implement)"
run-name: "Impl-Feature-Auto Implement · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
strict: false
sandbox:
  agent: false
features:
  dangerously-disable-sandbox-agent: "POC custom Anthropic endpoint cannot be expressed in AWF static egress allowlist; agent stays read-only and never holds the state PAT"
engine:
  id: claude
  model: claude-sonnet-4-6
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
  bash:
    - "git *"
    - "gh run download *"
    - "gh issue view *"
    - "cat:*"
    - "ls:*"
    - "mkdir:*"
    - "cp:*"
    - "python3 *"
    - "pytest *"
    - "uv *"
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

<!-- BOOTSTRAP: gh-aw runs `claude --print`, where the SessionStart hook may not
fire, so we inline the using-superpowers bootstrap to make the model reliably
reach for the staged skills. -->

You have superpowers. Skills live under `.claude/skills/` (staged as PROJECT
skills, so they are BARE-NAMED — `executing-plans`, not
`superpowers:executing-plans`). Before any creative work, check whether a skill
applies and use it.

# Implement Agent — execute the plan with TDD, then open ONE PR.

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

## 3. Execute the plan (TDD)
Use `executing-plans` / `subagent-driven-development` to implement the plan
task-by-task under RED-GREEN-REFACTOR. Run the project's tests. Any mid-implementation
ledger appends go into the spec doc that ships in the PR.

## 4. Finish the branch + open the PR
Use `finishing-a-development-branch`. Commit spec + plan + code + tests **in the root
repo** on `impl-feature-auto/issue-<N>` (the changes must be committed here for
safe-outputs to capture them). Open ONE pull request via safe-outputs. The PR body
MUST carry the Accountability Ledger and the READ-THESE-FIRST list (from the design
spec) so the PR is self-describing, and reference the issue (`Closes #<N>`).

## 5. Emit evidence
Write `/tmp/gh-aw/evidence.json` as ONE JSON object:
`{"summary":"<one line>","pr_branch":"impl-feature-auto/issue-<N>","run_id":"<GITHUB_RUN_ID>"}`
Write nothing else.

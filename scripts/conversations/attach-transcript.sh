#!/usr/bin/env bash
# Opt-in transcript capture for the context pipeline. On `git push`, upload THIS session's Claude
# transcript onto a dedicated orphan branch (default `conversations`, override CONVERSATIONS_BRANCH)
# at <owner>/<repo>/pr-<number>/<session>.jsonl — so it never pollutes the PR's own branch, and the
# CI context pipeline can locate this PR's transcript (keyed by repo + PR number).
#
# Wiring: register as a PostToolUse(Bash) command hook in your OWN Claude settings — see
# scripts/conversations/README.md. This repo git-ignores `.claude/`, so registration is per-user
# (`.claude/settings.local.json`), not committed.
#
# Scope: this uploads ONLY the current session, read from the hook payload's `transcript_path` /
# `session_id` (documented PostToolUse fields). It deliberately does NOT glob the project dir the
# way the upstream "custody" hook does — from a single shared working directory that would publish
# every unrelated session, which on a PUBLIC repo is a leak. One session per push, always the right one.
#
# Gating: only runs when CONTEXT_CAPTURE=1. Every failure path exits 0 — never blocks your push.
# Deps: `gh` (write via the API) and `jq` (parse the payload); skips cleanly if either is missing,
# or if there is no PR for the branch yet (push again after opening the PR).
#
# CAVEAT: transcripts live in git history on the dedicated branch and may contain sensitive content.
# On a PUBLIC repository they are world-readable. Opt-in only, and only for conversations you are
# comfortable publishing. See scripts/conversations/README.md.
set +e
[ "${CONTEXT_CAPTURE:-}" = "1" ] || exit 0
command -v gh >/dev/null 2>&1 || exit 0
command -v jq >/dev/null 2>&1 || exit 0

# PostToolUse(Bash) delivers the tool call as JSON on stdin.
input="$(cat 2>/dev/null)"
[ -n "$input" ] || exit 0

# Only act on a `git push`.
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)"
case "$cmd" in *'git push'*) ;; *) exit 0 ;; esac

# The current session's transcript, straight from the payload (no dir globbing, no guessing).
src="$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)"
session="$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null)"
[ -n "$src" ] && [ -f "$src" ] || exit 0
[ -n "$session" ] || session="$(basename "$src" .jsonl)"
session="${session//[^a-zA-Z0-9_-]/}"
[ -n "$session" ] || exit 0

dir="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$dir" 2>/dev/null || exit 0
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
[ -n "$branch" ] && [ "$branch" != "HEAD" ] || exit 0
case "$branch" in *..*) exit 0 ;; esac

# Resolve <owner>/<repo> for the repo this branch publishes to (where the PR lives), then the PR
# number. Capture must target the same repo the readers read — the fork you push to, NOT the
# upstream you forked from. Prefer an explicit override, else the branch's upstream remote, else origin.
repo_slug="${CONVERSATIONS_REPO:-}"
if [ -z "$repo_slug" ]; then
  up_remote="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null | cut -d/ -f1)"
  remote_url="$(git remote get-url "${up_remote:-origin}" 2>/dev/null)"
  repo_slug="$(printf '%s' "$remote_url" | sed -E 's#(\.git)?/?$##; s#^git@[^:]+:##; s#^[a-zA-Z]+://[^/]+/##')"
fi
case "$repo_slug" in */*) ;; *) exit 0 ;; esac                     # need owner/repo
case "$repo_slug" in *[!a-zA-Z0-9._/-]*) exit 0 ;; esac            # reject anything path-unsafe
pr="$(gh pr view "$branch" --repo "$repo_slug" --json number --jq .number 2>/dev/null)"
case "$pr" in ''|*[!0-9]*) exit 0 ;; esac                          # no PR yet (push again after opening it)

# Skip empties and anything over the 25 MiB cap (keep the branch lean).
sz="$(wc -c < "$src" 2>/dev/null || echo 0)"
[ "$sz" -gt 0 ] && [ "$sz" -le 26214400 ] || exit 0

# Write to the dedicated branch via the GitHub API (pure bash + gh; no working-tree commit).
# Source the storage helpers next to this script, so it works wherever the tree is checked out.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
. "$script_dir/conversations-store.sh" 2>/dev/null || exit 0
conv_branch="${CONVERSATIONS_BRANCH:-conversations}"
ensure_branch "$repo_slug" "$conv_branch" || exit 0

dest="${repo_slug}/pr-${pr}/${session}.jsonl"
# Skip when the branch already has identical content (git blob sha == Contents API sha), so
# repeated pushes of an unchanged session don't churn the branch.
local_sha="$(git hash-object "$src" 2>/dev/null)"
remote_sha="$(gh api "repos/${repo_slug}/contents/${dest}?ref=${conv_branch}" --jq .sha 2>/dev/null)"
[ -n "$local_sha" ] && [ "$local_sha" = "$remote_sha" ] && exit 0
put_file "$repo_slug" "$conv_branch" "$dest" "$src" "chore(conversations): ${repo_slug}#${pr} ${session}"
exit 0

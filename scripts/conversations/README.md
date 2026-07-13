# Conversation capture

Opt-in tooling to save a Claude Code session transcript for a PR, so the repo's CI context
pipeline can pick it up. It uploads the transcript to a dedicated `conversations` branch — it
never adds anything to your PR's own diff.

## Why

The CI context pipeline (`.github/workflows/context-agent.lock.yml`) already **reads** transcripts
from a dedicated `conversations` branch, at:

```
<owner>/<repo>/pr-<PR_NUMBER>/<session>.jsonl
```

via `scripts/context/locate.js` (`CONVERSATIONS_REF=conversations`,
`CONVERSATIONS_DIR=<owner>/<repo>/pr-<PR>`). This tooling is the **writer** for that same layout, so
every PR's conversation is stored the same way.

## Files

| File | Role |
|------|------|
| `attach-transcript.sh` | PostToolUse(Bash) hook. On `git push`, uploads the current session to the `conversations` branch. |
| `conversations-store.sh` | Storage helpers (`ensure_branch`, `put_file`) — pure `bash` + `gh api`. |
| `settings.example.json` | The hook-registration snippet to copy into your local settings. |

## How it works

1. Registered as a `PostToolUse` hook with `matcher: "Bash"`.
2. Runs only when `CONTEXT_CAPTURE=1`. Otherwise it exits immediately (no-op).
3. On a `git push`, it reads the **current session** from the hook payload
   (`transcript_path` / `session_id` — documented PostToolUse fields), resolves the PR number for
   the current branch with `gh`, and uploads that one `.jsonl` to
   `conversations:<owner>/<repo>/pr-<PR>/<session>.jsonl`.
4. Every failure path exits `0` — it never blocks your push. If there's no PR for the branch yet,
   it skips; push again after opening the PR.

**Only the current session is uploaded** — read straight from the hook payload, not by scanning
your project directory. This is deliberate: scanning would publish every unrelated session that
shares your working directory, which on a public repo is a leak.

## Requirements

- `gh` (authenticated) and `jq` on `PATH`. Missing either → the hook no-ops.

## Enabling it (per-user)

Capture is **off by default**, and because this repo git-ignores `.claude/`, registration is
per-user — nothing about enabling it is committed.

1. Copy the `hooks` block from `settings.example.json` into your own
   `.claude/settings.local.json` (per-user, git-ignored).
2. Opt in for a session:

   ```bash
   export CONTEXT_CAPTURE=1
   ```

3. Work as usual and `git push`. Once the PR exists, each push saves the current session to the
   `conversations` branch. (Push again after opening the PR if you pushed before it existed.)

## Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `CONTEXT_CAPTURE` | *(unset)* | Must be `1` to enable capture. |
| `CONVERSATIONS_BRANCH` | `conversations` | Branch the transcripts are stored on. |
| `CONVERSATIONS_REPO` | *(auto)* | `owner/repo` to write to. Auto-detected from the branch's upstream remote, else `origin`. |

## ⚠️ Privacy caveat

Transcripts are committed to git history on the `conversations` branch and **may contain sensitive
content** (tokens, paths, internal discussion). **`SiRumCz/yuanrong-datasystem` is a public repo, so
anything captured is world-readable.** Only enable this for conversations you are comfortable
publishing. There is a 25 MiB per-session cap; unchanged sessions are skipped on repeat pushes.

## One-off manual save (no hook)

You don't need the hook to save a single conversation. With `gh` authenticated:

```bash
source scripts/conversations/conversations-store.sh
REPO=SiRumCz/yuanrong-datasystem
PR=<pr-number>
SRC=<absolute path to the session .jsonl>       # e.g. ~/.claude/projects/<slug>/<session>.jsonl
SESSION=$(basename "$SRC" .jsonl)
ensure_branch "$REPO" conversations
put_file "$REPO" conversations "$REPO/pr-$PR/$SESSION.jsonl" "$SRC" \
  "chore(conversations): $REPO#$PR $SESSION"
```

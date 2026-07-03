#!/usr/bin/env bash
# Shared helpers to store a file on a dedicated GitHub branch via the API (`gh`).
# Pure bash + `gh api`; no Node, no app involvement. Sourced by attach-transcript.sh.
# Functions return non-zero on failure; the caller decides what to do (the capture hook
# ignores failures so it never blocks a push).
#
# Layout produced by callers: transcripts land on a dedicated orphan branch (default
# `conversations`) at <owner>/<repo>/pr-<number>/<session>.jsonl. This is the layout the
# repo's CI context pipeline reads (see .github/workflows/context-agent.lock.yml, which
# points locate.js at CONVERSATIONS_REF=conversations / CONVERSATIONS_DIR=<repo>/pr-<PR>).

# ensure_branch <repo> <branch> : create <branch> as an orphan (parentless) branch if missing.
ensure_branch() {
  local repo="$1" branch="$2" api_base="repos/$1"
  gh api "${api_base}/git/ref/heads/${branch}" >/dev/null 2>&1 && return 0
  local blob tree commit tbody cbody
  blob="$(gh api -X POST "${api_base}/git/blobs" -f content='conversations branch' -f encoding=utf-8 --jq .sha 2>/dev/null)" || return 1
  [ -n "$blob" ] || return 1
  tbody="$(mktemp)" || return 1
  printf '{"tree":[{"path":"README.md","mode":"100644","type":"blob","sha":"%s"}]}' "$blob" > "$tbody"
  tree="$(gh api -X POST "${api_base}/git/trees" --input "$tbody" --jq .sha 2>/dev/null)"; rm -f "$tbody"
  [ -n "$tree" ] || return 1
  cbody="$(mktemp)" || return 1
  printf '{"message":"init conversations branch","tree":"%s","parents":[]}' "$tree" > "$cbody"
  commit="$(gh api -X POST "${api_base}/git/commits" --input "$cbody" --jq .sha 2>/dev/null)"; rm -f "$cbody"
  [ -n "$commit" ] || return 1
  gh api -X POST "${api_base}/git/refs" -f "ref=refs/heads/${branch}" -f "sha=${commit}" >/dev/null 2>&1 || return 1
}

# put_file <repo> <branch> <dest> <srcfile> <message> : create/update <dest> on <branch> with
# <srcfile>'s contents. Compare-and-set on the existing blob sha; refetch + retry once on 409.
# The request body is assembled by `gh api` from -f/-F fields — gh handles JSON escaping and reads
# the base64 content straight from a file (-F content=@<file>), so large transcripts and arbitrary
# messages are safe. (A hand-rolled printf body produced "400 Problems parsing JSON" on ~1 MB
# payloads — gh-built fields avoid that entirely and drop the no-quote/backslash caveat on <message>.)
put_file() {
  local repo="$1" branch="$2" dest="$3" src="$4" msg="$5" api_base="repos/$1"
  local b64 cur rc
  b64="$(mktemp)" || return 1
  base64 < "$src" | tr -d '\n' > "$b64" || { rm -f "$b64"; return 1; }
  # _conv_put closes over $b64, $api_base, $dest, $msg, and $branch from put_file's scope.
  _conv_put() {   # $1 = sha ("" to create)
    local sha="$1"
    # gh serializes -f/-F into the JSON body; -F content=@<file> streams the base64 from disk
    # (no shell-built body), so payload size and special characters can't corrupt the request.
    if [ -n "$sha" ]; then
      gh api -X PUT "${api_base}/contents/${dest}" \
        -f "message=${msg}" -f "branch=${branch}" -F "content=@${b64}" -f "sha=${sha}" >/dev/null 2>&1
    else
      gh api -X PUT "${api_base}/contents/${dest}" \
        -f "message=${msg}" -f "branch=${branch}" -F "content=@${b64}" >/dev/null 2>&1
    fi
  }
  cur="$(gh api "${api_base}/contents/${dest}?ref=${branch}" --jq .sha 2>/dev/null)"
  if _conv_put "$cur"; then rm -f "$b64"; return 0; fi
  cur="$(gh api "${api_base}/contents/${dest}?ref=${branch}" --jq .sha 2>/dev/null)"   # stale sha → refetch
  _conv_put "$cur"; rc=$?; rm -f "$b64"; return $rc
}

#!/usr/bin/env python3
"""Non-interactive GitHub PR reviewer for the ds-pr-review skill.

Replaces the human/agent prepare→read→write→publish loop with a single automated
pass driven by an OpenAI-compatible gateway, suitable for a GitHub Actions check:

  1. Build the review bundle (PR files + annotated patch with ``diff_line_index``)
     by reusing the existing ``context_builder`` / ``diff_position`` /
     ``comment_formatter`` modules, sourcing PR data from ``github_api`` (with a
     ``gh`` CLI fallback).
  2. Call the gateway **once** (chat/completions, model ``gpt-5.5``) with a prompt
     derived from SKILL.md's rubric + the bundle, asking for findings JSON that
     matches the existing contract.
  3. Validate via ``finding_validator`` and dedupe via ``dedupe``.
  4. Publish inline review comments via ``github_api`` (or print them under
     ``DRY_RUN``).

ADVISORY BY DESIGN: any failure (LLM down, parse error, API error, anchor miss)
results in posting nothing and exiting 0. This check must NEVER block a PR.

Env:
  GITHUB_REPOSITORY   owner/repo                (required for live)
  PR_NUMBER           PR number                 (or GITHUB_PR_NUMBER)
  GITHUB_TOKEN        Actions token             (required for live)
  OPENAI_API_KEY      gateway key               (required; CODEX_API_KEY accepted)
  OPENAI_BASE_URL     gateway base, default the custody Tailscale gateway
  OPENAI_MODEL        model id, default gpt-5.5
  DRY_RUN=1           print rendered comments instead of posting
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


# Make the sibling skill modules importable when invoked from the repo root in
# Actions (cwd = repo root). Both this dir and the cwd are added.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from comment_formatter import format_comment  # noqa: E402
from context_builder import build_context_snippets, focus_tags_for_path  # noqa: E402
from dedupe import (  # noqa: E402
    compress_suggestions,
    extract_fingerprint,
    fingerprint_for_finding,
)
from diff_position import (  # noqa: E402
    find_position,
    parse_patch,
    render_annotated_patch,
)
from finding_validator import validate_findings  # noqa: E402
from language_detect import detect_review_language  # noqa: E402


DEFAULT_BASE_URL = "https://arcyleung-ubuntu.tailb940e6.ts.net/v1/"
DEFAULT_MODEL = "gpt-5.5"

# Conservative bundle limits so a huge PR does not blow the model context.
SNIPPET_RADIUS = 18
MAX_SNIPPETS_PER_FILE = 3
MAX_CHARS_PER_SNIPPET = 2500
SUGGESTION_LIMIT_PER_FILE = 2
MAX_FILES_IN_PROMPT = 40
MAX_ANNOTATED_PATCH_CHARS = 6000


def _log(message: str) -> None:
    print(f"ds-pr-review: {message}", flush=True)


def _env_pr_number() -> int | None:
    for name in ("PR_NUMBER", "GITHUB_PR_NUMBER", "INPUT_PR_NUMBER"):
        raw = os.environ.get(name, "").strip()
        if raw.isdigit():
            return int(raw)
    return None


def _env_repo() -> tuple[str, str] | None:
    slug = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in slug:
        owner, repo = slug.split("/", 1)
        return owner, repo
    return None


# --------------------------------------------------------------------------- #
# PR data sourcing: github_api primary, gh CLI fallback.
# --------------------------------------------------------------------------- #


def _gh_json(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def _fetch_pr_data(owner: str, repo: str, number: int) -> dict[str, Any]:
    """Return {pull, files, comments} using github_api, falling back to gh CLI."""
    try:
        from github_api import GitHubClient

        client = GitHubClient.from_env()
        pull = client.get_pull(number)
        files = client.list_pull_files(number)
        comments = client.list_pull_comments(number)
        return {"pull": pull, "files": files, "comments": comments, "client": client}
    except Exception as exc:  # fall back to gh CLI
        _log(f"github_api unavailable ({exc}); falling back to gh CLI.")

    repo_slug = f"{owner}/{repo}"
    pull = _gh_json(
        ["pr", "view", str(number), "--repo", repo_slug, "--json",
         "number,title,body,state,author,baseRefName,headRefName,url,files"]
    ) or {}
    # gh returns files as [{path, additions, deletions}]; patches need the diff.
    raw_files = pull.get("files") or []
    diff_text = ""
    try:
        proc = subprocess.run(
            ["gh", "pr", "diff", str(number), "--repo", repo_slug],
            text=True, capture_output=True, check=True,
        )
        diff_text = proc.stdout
    except Exception as exc:  # pragma: no cover - network dependent
        _log(f"gh pr diff failed: {exc}")

    files = _files_from_unified_diff(raw_files, diff_text)
    return {
        "pull": {
            "number": number,
            "title": pull.get("title"),
            "body": pull.get("body"),
            "state": pull.get("state"),
            "user": {"login": (pull.get("author") or {}).get("login")},
            "base": {"ref": pull.get("baseRefName")},
            "head": {"ref": pull.get("headRefName")},
            "html_url": pull.get("url"),
        },
        "files": files,
        "comments": [],
        "client": None,
    }


def _files_from_unified_diff(raw_files: list[dict[str, Any]], diff_text: str) -> list[dict[str, Any]]:
    """Split a unified diff into per-file {filename, patch} entries."""
    per_file: dict[str, list[str]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            current = m.group(2) if m else None
            if current is not None:
                per_file.setdefault(current, [])
            continue
        if current is None:
            continue
        # Keep only the hunk body (skip index/---/+++ headers; parse_patch wants @@).
        if line.startswith(("index ", "--- ", "+++ ", "new file", "deleted file",
                            "similarity ", "rename ", "old mode", "new mode")):
            continue
        per_file[current].append(line)

    known = {str(f.get("path") or f.get("filename") or ""): f for f in raw_files}
    out: list[dict[str, Any]] = []
    names = list(per_file.keys()) or list(known.keys())
    for name in names:
        meta = known.get(name, {})
        out.append(
            {
                "filename": name,
                "status": meta.get("status"),
                "additions": meta.get("additions"),
                "deletions": meta.get("deletions"),
                "changes": meta.get("changes"),
                "patch": "\n".join(per_file.get(name, [])),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Bundle building (mirrors review_pr._prepare, GitHub-sourced).
# --------------------------------------------------------------------------- #


def _build_bundle(pr_data: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    pull = pr_data["pull"]
    files = pr_data["files"]
    comments = pr_data["comments"]
    pr_description = str(pull.get("body") or pull.get("description") or "")
    language = detect_review_language(f"{pull.get('title') or ''}\n{pr_description}")

    review_files: list[dict[str, Any]] = []
    warnings: list[str] = []
    for file_info in files:
        path = str(file_info.get("filename") or file_info.get("path") or "")
        patch = str(file_info.get("patch") or "")
        position_map = parse_patch(patch)
        local_file = repo_root / path if repo_root else None
        snippets = build_context_snippets(
            local_file=local_file,
            position_map=position_map,
            snippet_radius=SNIPPET_RADIUS,
            max_snippets_per_file=MAX_SNIPPETS_PER_FILE,
            max_chars_per_snippet=MAX_CHARS_PER_SNIPPET,
        )
        if not patch:
            warnings.append(f"{path} has no patch payload; review relies on local context only.")
        review_files.append(
            {
                "path": path,
                "status": file_info.get("status"),
                "additions": file_info.get("additions"),
                "deletions": file_info.get("deletions"),
                "changes": file_info.get("changes"),
                "patch": patch,
                "annotated_patch": render_annotated_patch(position_map),
                "position_map": position_map,
                "focus_tags": focus_tags_for_path(path),
                "local_path": str(local_file) if local_file and local_file.exists() else None,
                "context_snippets": snippets,
            }
        )

    normalized_comments = []
    for comment in comments:
        body = str(comment.get("body") or "")
        normalized_comments.append(
            {
                "id": comment.get("id"),
                "path": comment.get("path"),
                "position": comment.get("position"),
                "body": body,
                "fingerprint": extract_fingerprint(body),
            }
        )

    return {
        "pr": {
            "number": int(pull.get("number")),
            "url": pull.get("html_url"),
            "title": pull.get("title"),
            "description": pr_description,
            "state": pull.get("state"),
            "author": (pull.get("user") or {}).get("login"),
            "base_ref": (pull.get("base") or {}).get("ref"),
            "head_ref": (pull.get("head") or {}).get("ref"),
            "language": language,
        },
        "warnings": warnings,
        "review_policy": {
            "need_to_resolve": False,
            "suggestion_limit_per_file": SUGGESTION_LIMIT_PER_FILE,
        },
        "files": review_files,
        "existing_comments": normalized_comments,
    }


# --------------------------------------------------------------------------- #
# Prompt + LLM call.
# --------------------------------------------------------------------------- #


_SYSTEM_PROMPT = """\
You are a senior reviewer for the openeuler/yuanrong-datasystem distributed data \
system (C++/Python/Java, hot-path SDK, worker/master infrastructure). Review the \
provided pull-request diff strictly for: correctness, data integrity, recovery, \
availability, hot-path performance, concurrency and C++ memory safety, internal/\
public API design and misuse prevention, ownership/lifetime, build closure \
(Bazel/CMake), public API/config/docs coverage, sensitive-information exposure, \
and tests.

Rules:
- Lead with the highest-impact findings. Only report findings you can back with \
concrete evidence from the diff or context snippets.
- Use severity `critical` only for correctness/safety/performance/build/contract \
breakage; use `warning` for real but non-blocking issues; use `suggestion` for \
localized cleanup. Do NOT report purely stylistic nits.
- Never quote secrets/credentials/private hosts verbatim; identify category + \
location instead.
- Match the comment language to the PR: write all natural-language fields in \
{language_name} ({language_code}).
- Anchor each finding to a changed line using `diff_line_index` from the \
annotated patch (the `diff=N` tag). Add `line` (new-file line number) as a \
fallback. Findings without a valid `diff_line_index` will be posted as general \
comments, so prefer anchoring.
- Put any multi-line code ONLY in `example_code` (raw code, no Markdown fences). \
Do not put fenced code blocks in other fields.

Return ONLY a JSON object, no prose, with this exact shape:
{{
  "overall_risk": "low|medium|high",
  "findings": [
    {{
      "path": "<repo-relative path>",
      "diff_line_index": <int>,
      "line": <int>,
      "type": "bug|build|compatibility|correctness|security|performance|design|documentation|test",
      "severity": "critical|warning|suggestion",
      "title": "<short title>",
      "evidence": "<concrete evidence>",
      "problem": "<what is wrong>",
      "impact": "<why it matters>",
      "suggestion": "<concrete fix direction>",
      "example_code": "<optional raw code sketch>",
      "verification": "<optional test/build/manual check>"
    }}
  ]
}}
If you find no defensible issues, return {{"overall_risk": "low", "findings": []}}.
"""


def _build_user_prompt(bundle: dict[str, Any]) -> str:
    pr = bundle["pr"]
    lines: list[str] = []
    lines.append(f"PR #{pr['number']}: {pr.get('title') or ''}")
    if pr.get("description"):
        desc = pr["description"]
        lines.append("Description:\n" + (desc[:2000] + ("…" if len(desc) > 2000 else "")))
    lines.append("")
    lines.append("Changed files (annotated patch; use diff= tag as diff_line_index):")
    for f in bundle["files"][:MAX_FILES_IN_PROMPT]:
        lines.append("")
        lines.append(f"### {f['path']}  [{', '.join(f.get('focus_tags') or [])}]")
        ap = f.get("annotated_patch") or "(no patch)"
        if len(ap) > MAX_ANNOTATED_PATCH_CHARS:
            ap = ap[:MAX_ANNOTATED_PATCH_CHARS] + "\n... [truncated]"
        lines.append(ap)
        for snip in f.get("context_snippets") or []:
            lines.append(f"-- context {f['path']} lines {snip['start_line']}-{snip['end_line']} --")
            lines.append(snip["content"])
    if len(bundle["files"]) > MAX_FILES_IN_PROMPT:
        lines.append(f"\n[... {len(bundle['files']) - MAX_FILES_IN_PROMPT} more files omitted ...]")
    return "\n".join(lines)


def _call_llm(bundle: dict[str, Any]) -> dict[str, Any] | None:
    api_key = (os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY") or "").strip()
    if not api_key:
        _log("no LLM key in env; skipping (advisory).")
        return None

    base_url = (os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
    language = "zh" if bundle["pr"].get("language") == "zh" else "en"
    language_name = "Chinese" if language == "zh" else "English"

    system_prompt = _SYSTEM_PROMPT.format(language_name=language_name, language_code=language)
    user_prompt = _build_user_prompt(bundle)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        _log(f"LLM request failed: {exc.code} {detail}")
        return None
    except Exception as exc:  # pragma: no cover - network dependent
        _log(f"LLM request error: {exc}")
        return None

    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
        return _parse_findings_json(content)
    except Exception as exc:
        _log(f"could not parse LLM response: {exc}")
        return None


def _parse_findings_json(content: str) -> dict[str, Any] | None:
    content = content.strip()
    # Strip a ```json ... ``` fence if the model added one despite instructions.
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
        content = re.sub(r"\n?```$", "", content).strip()
    try:
        doc = json.loads(content)
    except json.JSONDecodeError:
        # Last resort: grab the outermost {...}.
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return None
        doc = json.loads(m.group(0))
    if not isinstance(doc, dict) or not isinstance(doc.get("findings"), list):
        return None
    return doc


# --------------------------------------------------------------------------- #
# Publish (mirrors review_pr._publish, GitHub-anchored by diff position).
# --------------------------------------------------------------------------- #


def _bundle_file(bundle: dict[str, Any], path: str) -> dict[str, Any] | None:
    for entry in bundle.get("files", []):
        if entry.get("path") == path:
            return entry
    return None


def _publish(bundle: dict[str, Any], findings_doc: dict[str, Any], client: Any, dry_run: bool) -> int:
    findings = list(findings_doc.get("findings") or [])
    if not findings:
        _log("no findings to post.")
        return 0

    language = "zh" if bundle["pr"].get("language") == "zh" else "en"
    validation_errors = validate_findings(findings, language)
    if validation_errors:
        _log("findings failed validation; posting nothing (advisory):")
        for err in validation_errors[:20]:
            _log(f"  - {err}")
        return 0

    findings = compress_suggestions(findings, int(bundle["review_policy"]["suggestion_limit_per_file"]))

    existing_fingerprints: set[str] = set()
    for comment in bundle.get("existing_comments", []):
        fp = extract_fingerprint(str(comment.get("body") or "")) or comment.get("fingerprint")
        if fp:
            existing_fingerprints.add(str(fp))

    posted_line = posted_general = skipped_dupes = skipped_suggestions = 0
    pr_number = int(bundle["pr"]["number"])

    for finding in findings:
        fingerprint = fingerprint_for_finding(finding)
        if fingerprint in existing_fingerprints:
            skipped_dupes += 1
            continue

        body = format_comment(finding, language, fingerprint)
        file_entry = _bundle_file(bundle, str(finding.get("path", "")))
        position_entry = None
        diff_position = None
        if file_entry:
            position_entry = find_position(
                position_map=file_entry.get("position_map", []),
                diff_line_index=finding.get("diff_line_index"),
                line=finding.get("line"),
                match_text=finding.get("match_text"),
            )
            if position_entry is not None:
                diff_position = int(position_entry["position"])

        try:
            if file_entry and position_entry is not None and diff_position is not None:
                if dry_run:
                    _print_dry_run("line", file_entry["path"], body, diff_position)
                else:
                    client.post_pull_comment(
                        number=pr_number,
                        body=body,
                        path=file_entry["path"],
                        absolute_line=diff_position,  # GitHub diff position
                        need_to_resolve=False,
                    )
                posted_line += 1
            else:
                if dry_run:
                    _print_dry_run("general", finding.get("path"), body, None)
                else:
                    client.post_general_comment(pr_number, body)
                posted_general += 1
        except Exception as exc:
            # Never let one comment failure block the PR; downgrade to skip.
            _log(f"failed to post finding for {finding.get('path')}: {exc}")
            if finding.get("severity") == "suggestion":
                skipped_suggestions += 1
            continue

        existing_fingerprints.add(fingerprint)

    _log(
        f"summary: overall_risk={findings_doc.get('overall_risk', 'low')} "
        f"line_comments={posted_line} general_comments={posted_general} "
        f"skipped_duplicates={skipped_dupes} skipped_suggestions={skipped_suggestions} "
        f"dry_run={dry_run}"
    )
    return 0


def _print_dry_run(mode: str, path: Any, body: str, position: int | None) -> None:
    print("\n" + "=" * 72)
    print(f"[DRY_RUN] mode={mode} path={path} position={position}")
    print("-" * 72)
    print(body)


# --------------------------------------------------------------------------- #


def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false", "False")

    repo = _env_repo()
    number = _env_pr_number()
    if not repo or number is None:
        _log("missing GITHUB_REPOSITORY or PR number; nothing to do (advisory).")
        return 0
    owner, repo_name = repo

    repo_root = Path(os.environ.get("GITHUB_WORKSPACE") or os.getcwd()).resolve()

    try:
        pr_data = _fetch_pr_data(owner, repo_name, number)
    except Exception as exc:
        _log(f"could not fetch PR data: {exc}; posting nothing (advisory).")
        return 0

    try:
        bundle = _build_bundle(pr_data, repo_root)
    except Exception as exc:
        _log(f"could not build review bundle: {exc}; posting nothing (advisory).")
        return 0

    findings_doc = _call_llm(bundle)
    if findings_doc is None:
        _log("no findings produced; check stays green (advisory).")
        return 0

    client = pr_data.get("client")
    if not dry_run and client is None:
        # No live client (gh-only fallback path); we can still build but not post
        # via github_api — try to construct one, else skip posting.
        try:
            from github_api import GitHubClient

            client = GitHubClient.from_env()
        except Exception as exc:
            _log(f"no GitHub client for posting ({exc}); printing instead.")
            dry_run = True

    try:
        return _publish(bundle, findings_doc, client, dry_run)
    except Exception as exc:
        _log(f"publish failed: {exc}; check stays green (advisory).")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

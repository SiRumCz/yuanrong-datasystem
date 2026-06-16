#!/usr/bin/env python3
"""GitHub forge adapter for the ds-pr-review skill.

Mirrors the public interface of ``gitcode_api.GitCodeClient`` so the rest of the
review pipeline (bundle building, dedupe, publishing) stays forge-agnostic. The
only behavioural difference is the meaning of the integer line argument to
``post_pull_comment``: GitHub anchors inline review comments by the **diff
position** (the 1-based line index within the file's unified-diff hunk), which is
exactly the ``position`` value produced by ``diff_position.parse_patch``. The
GitCode adapter overloaded the same argument as an absolute file line; the
GitHub runner therefore passes the diff position instead.

Uses only the Python standard library (``urllib``) so the workflow needs no pip
install. Repo and PR are taken from the environment (``GITHUB_REPOSITORY``,
``PR_NUMBER``/``GITHUB_PR_NUMBER``) but may be passed explicitly.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_API_BASE = "https://api.github.com"
_USER_AGENT = "ds-pr-review-github-skill/0.1"


class GitHubError(RuntimeError):
    """Raised when the GitHub API cannot satisfy a request."""


class GitHubClient:
    """Minimal GitHub REST client matching ``GitCodeClient``'s surface."""

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        base_url: str = DEFAULT_API_BASE,
    ):
        self.owner = owner
        self.repo = repo
        self.token = token
        self.base_url = (base_url or DEFAULT_API_BASE).rstrip("/")

    # -- construction helpers -------------------------------------------------

    @classmethod
    def from_env(cls) -> "GitHubClient":
        """Build a client from the GitHub Actions environment.

        Reads ``GITHUB_REPOSITORY`` (``owner/repo``) and the Actions token from
        ``GITHUB_TOKEN``/``GH_TOKEN``. ``GITHUB_API_URL`` overrides the API base
        (GitHub Enterprise).
        """
        repo_slug = os.environ.get("GITHUB_REPOSITORY", "").strip()
        if "/" not in repo_slug:
            raise GitHubError(
                "GITHUB_REPOSITORY must be set to '<owner>/<repo>' in the Actions env."
            )
        owner, repo = repo_slug.split("/", 1)

        token = ""
        for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
            token = os.environ.get(env_name, "").strip()
            if token:
                break
        if not token:
            raise GitHubError("No GitHub token found (expected GITHUB_TOKEN / GH_TOKEN).")

        base_url = os.environ.get("GITHUB_API_URL", DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
        return cls(owner=owner, repo=repo, token=token, base_url=base_url)

    # -- low-level HTTP -------------------------------------------------------

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Accept": accept,
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": _USER_AGENT,
        }

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> Any:
        body = None if data is None else json.dumps(data).encode("utf-8")
        headers = self._headers(accept)
        if body is not None:
            headers["Content-Type"] = "application/json"

        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                if not payload:
                    return None
                # The diff media type returns text/plain, not JSON.
                if accept.endswith("diff") or accept.endswith("patch"):
                    return payload.decode("utf-8", errors="replace")
                return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover - best effort
                pass
            raise GitHubError(f"GitHub API {method} {path} failed: {exc.code} {detail}") from exc

    def _paginate(self, path: str) -> list[dict[str, Any]]:
        page = 1
        items: list[dict[str, Any]] = []
        sep = "&" if "?" in path else "?"
        while True:
            query = urllib.parse.urlencode({"page": page, "per_page": 100})
            payload = self._request("GET", f"{path}{sep}{query}")
            if not payload:
                break
            if not isinstance(payload, list):
                raise GitHubError(f"Expected list response for {path}, got {type(payload)!r}")
            items.extend(payload)
            if len(payload) < 100:
                break
            page += 1
        return items

    def _repo_path(self, suffix: str) -> str:
        return f"/repos/{self.owner}/{self.repo}{suffix}"

    # -- public surface (mirrors GitCodeClient) -------------------------------

    def get_pull(self, number: int) -> dict[str, Any]:
        return self._request("GET", self._repo_path(f"/pulls/{number}"))

    def list_pull_files(self, number: int) -> list[dict[str, Any]]:
        """Changed files; each entry carries GitHub's ``filename`` + ``patch``.

        ``review_pr`` reads ``filename``/``path`` and ``patch`` from these
        entries, which GitHub already supplies in the same shape.
        """
        return self._paginate(self._repo_path(f"/pulls/{number}/files"))

    def get_pull_diff(self, number: int) -> str:
        """Fetch the full unified diff for the PR (text/plain)."""
        return self._request(
            "GET",
            self._repo_path(f"/pulls/{number}"),
            accept="application/vnd.github.v3.diff",
        )

    def list_pull_comments(self, number: int) -> list[dict[str, Any]]:
        """Existing inline **review** comments + issue (general) comments.

        Both are merged so the dedupe fingerprint scan sees every previously
        posted ds-pr-review comment regardless of which channel it used.
        """
        review_comments = self._paginate(self._repo_path(f"/pulls/{number}/comments"))
        issue_comments = self._paginate(self._repo_path(f"/issues/{number}/comments"))
        return [*review_comments, *issue_comments]

    def post_pull_comment(
        self,
        number: int,
        body: str,
        path: str,
        absolute_line: int,
        need_to_resolve: bool = False,
    ) -> Any:
        """Post an inline review comment anchored at a diff ``position``.

        Signature matches ``GitCodeClient.post_pull_comment`` so the publisher is
        forge-agnostic, but ``absolute_line`` here is the GitHub **diff
        position** (1-based index into the file's unified diff). ``need_to_resolve``
        has no GitHub equivalent and is accepted-and-ignored for compatibility.

        A single-comment review must reference a commit SHA; the PR head SHA is
        fetched lazily and cached.
        """
        head_sha = self._head_sha(number)
        payload = {
            "body": body,
            "commit_id": head_sha,
            "path": path,
            "position": int(absolute_line),
        }
        return self._request(
            "POST",
            self._repo_path(f"/pulls/{number}/comments"),
            payload,
        )

    def post_general_comment(self, number: int, body: str) -> Any:
        """Post a PR-level (issue) comment — used for the summary / unanchored notes."""
        return self._request(
            "POST",
            self._repo_path(f"/issues/{number}/comments"),
            {"body": body},
        )

    # -- internal -------------------------------------------------------------

    _cached_head_sha: str | None = None

    def _head_sha(self, number: int) -> str:
        if self._cached_head_sha:
            return self._cached_head_sha
        pull = self.get_pull(number)
        sha = str(((pull.get("head") or {}).get("sha")) or "")
        if not sha:
            raise GitHubError(f"Could not resolve head SHA for PR #{number}.")
        self._cached_head_sha = sha
        return sha

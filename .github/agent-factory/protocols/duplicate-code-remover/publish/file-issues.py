#!/usr/bin/env python3
"""file-issues (zone 4, trusted) — after detect's checks pass, file one GitHub
issue per duplication pattern (dedup by fingerprint marker), then post a
`/impl-feature-auto` comment on each so the auto-fixer opens a PR.

ABI: file-issues.py <evidence.json> <instance-key>; env ENGINE_LOCAL,
GITHUB_REPOSITORY, PUBLISH_TOKEN, PR. Prints {"conclusion","summary"}.

CRITICAL: the comment is posted with PUBLISH_TOKEN (a PAT). The default
GITHUB_TOKEN cannot trigger downstream workflows, so a comment posted by it
would silently fail to fire impl-feature-auto."""
import hashlib
import json
import os
import re
import subprocess
import sys

_SEV_ORDER = {"high": 0, "medium": 1, "low": 2}
_WS = re.compile(r"\s+")


def _local():
    return os.environ.get("ENGINE_LOCAL", "0") == "1"


def _collapse(s):
    return _WS.sub(" ", s or "").strip()


def fingerprint(pattern):
    parts = sorted(
        f"{loc.get('path','')}::{_collapse(loc.get('existing_code',''))}"
        for loc in (pattern.get("locations", []) or [])
        if isinstance(loc, dict)
    )
    h = hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()
    return f"dup-fp:{h}"


def comment_body():
    return ("/impl-feature-auto\n\n"
            "_Filed by duplicate-code-remover. Please remove this duplication "
            "(extract the shared logic; keep behaviour identical)._")


def issue_title(pattern):
    return f"[duplicate-code] {pattern.get('name','duplication')}"


def issue_body(pattern, fp):
    lines = [f"# 🔍 Duplicate code: {pattern.get('name','')}", ""]
    lines.append(f"**Severity:** {pattern.get('severity','?')}")
    lines.append("")
    lines.append(pattern.get("rationale", ""))
    lines.append("")
    lines.append("## Locations")
    for loc in pattern.get("locations", []) or []:
        if not isinstance(loc, dict):
            continue
        lines.append(f"- `{loc.get('path')}` (lines {loc.get('start_line')}–{loc.get('end_line')})")
        lines.append("")
        lines.append("  ```cpp")
        for cl in (loc.get("existing_code", "") or "").split("\n"):
            lines.append("  " + cl)
        lines.append("  ```")
    lines.append("")
    lines.append("## Requested change")
    lines.append("Extract the duplicated logic into a single shared definition and "
                 "update all call sites. Preserve behaviour; add/keep tests.")
    lines.append("")
    lines.append(f"<!-- {fp} -->")
    return "\n".join(lines)


def _gh_env():
    env = dict(os.environ)
    tok = os.environ.get("PUBLISH_TOKEN", "")
    if tok:
        env["GH_TOKEN"] = tok
    return env


def _existing_fingerprints(repo):
    """Open issues whose body carries a dup-fp marker -> set of markers (dedupe).

    Returns None if the query itself FAILED (non-zero exit or unparsable JSON) so
    the caller can fail safe instead of silently treating a transient failure as
    "no duplicates exist" (which would re-file already-filed issues on the daily
    cron). Returns a set (possibly empty) when the query succeeded.
    """
    r = subprocess.run(
        ["gh", "issue", "list", "--repo", repo, "--state", "open",
         "--search", "dup-fp in:body", "--json", "body", "--limit", "100"],
        text=True, capture_output=True, env=_gh_env(),
    )
    if r.returncode != 0:
        sys.stderr.write(f"[file-issues] list failed: {(r.stderr or '').strip()}\n")
        return None
    found = set()
    if r.stdout.strip():
        try:
            for it in json.loads(r.stdout):
                for m in re.findall(r"dup-fp:[0-9a-f]{40}", it.get("body", "") or ""):
                    found.add(m.split(":", 1)[1])
        except ValueError as e:
            sys.stderr.write(f"[file-issues] list parse failed: {e}\n")
            return None
    return found


def _create_issue(repo, title, body, labels):
    r = subprocess.run(
        ["gh", "issue", "create", "--repo", repo, "--title", title,
         "--body", body, "--label", ",".join(labels)],
        text=True, capture_output=True, env=_gh_env(),
    )
    if r.returncode != 0:
        sys.stderr.write(f"[file-issues] create failed: {r.stderr.strip()}\n")
        return ""
    # gh prints the issue URL; extract the trailing number
    m = re.search(r"/issues/(\d+)", r.stdout.strip())
    return m.group(1) if m else ""


def _post_comment(repo, number, body):
    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}"],
        text=True, capture_output=True, env=_gh_env(),
    )
    if r.returncode != 0:
        sys.stderr.write(f"[file-issues] comment failed: {r.stderr.strip()}\n")


def main():
    try:
        _main()
    except Exception as e:  # belt-and-suspenders: the ABI requires exactly one
        # JSON {"conclusion","summary"} on stdout, always — even on a malformed
        # pattern that slipped past the checks (e.g. a non-list `locations`).
        print(json.dumps({"conclusion": "neutral", "summary": f"publish hook error: {e}"}))


def _main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    instance = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        with open(ev_path) as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        ev = {}
    patterns = [p for p in (ev.get("patterns") or []) if isinstance(p, dict)]
    if not patterns:
        print(json.dumps({"conclusion": "neutral",
                          "summary": f"No duplication patterns to file ({instance})."}))
        return

    patterns.sort(key=lambda p: _SEV_ORDER.get(p.get("severity"), 9))
    try:
        cap = int(os.environ.get("DUP_MAX_PATTERNS", "3"))
    except ValueError:
        cap = 3
    patterns = patterns[:cap]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    labels = ["code-quality", "automated-analysis"]

    if _local():
        for p in patterns:
            fp = fingerprint(p)
            sys.stderr.write(f"[ENGINE_LOCAL] would create issue '{issue_title(p)}' [{fp}]\n")
            sys.stderr.write(f"[ENGINE_LOCAL] would comment: {comment_body().splitlines()[0]}\n")
        print(json.dumps({"conclusion": "success",
                          "summary": f"[local] {len(patterns)} issue(s) + /impl-feature-auto for {instance}."}))
        return

    seen = _existing_fingerprints(repo)
    if seen is None:
        # Dedupe query failed: fail safe -> file nothing this run rather than risk
        # re-creating already-filed issues (this hook runs on a daily cron).
        print(json.dumps({
            "conclusion": "neutral",
            "summary": f"dedupe query failed; skipped issue creation to avoid duplicates ({instance})",
        }))
        return
    created, skipped = [], 0
    for p in patterns:
        fp = fingerprint(p)
        if fp.split(":", 1)[1] in seen:
            skipped += 1
            continue
        num = _create_issue(repo, issue_title(p), issue_body(p, fp), labels)
        if num:
            _post_comment(repo, num, comment_body())
            created.append(num)
    summary = f"Filed {len(created)} issue(s) (#{', #'.join(created) or '—'}); {skipped} deduped."
    print(json.dumps({"conclusion": "success" if created or skipped else "failure",
                      "summary": summary}))


if __name__ == "__main__":
    main()

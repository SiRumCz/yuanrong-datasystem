#!/usr/bin/env python3
"""Patch-applier for the demo fix phase. Each fix carries a unified `git diff`
of the edit-and-test agent's own change for that cluster (see
fix.evidence.schema.json) -- not a single-anchor suggested_patch/original_line
pair. conclude-fix clones the PR head into a git workdir before calling
apply_all, so we apply each fix's diff there with `git apply` rather than
editing files by hand.

Safety model: `git apply --check` runs first (a dry run that writes nothing) to
decide applied vs skipped; only if that succeeds do we run the real apply. A
diff that is missing/empty, malformed, or doesn't match the current file
content (stale context, already applied, wrong file) is skipped with a clear
detail -- never a partial write.

apply_all maps over a list of fixes and returns one result dict each.
"""
import re
import subprocess

_PLUS_HEADER_RE = re.compile(r"^\+\+\+ (.+)$", re.MULTILINE)


def _diff_paths(diff):
    """Best-effort list of paths a unified diff touches, read from its `+++`
    headers. Used only to scope the commit after a successful apply."""
    paths = []
    for m in _PLUS_HEADER_RE.finditer(diff):
        path = m.group(1).strip()
        if path == "/dev/null":
            continue
        if path.startswith("b/"):
            path = path[2:]
        paths.append(path)
    return paths


def apply_fix(workdir, fix):
    cid = fix.get("cluster_id")
    diff = fix.get("diff")
    out = {"cluster_id": cid, "status": "skipped", "detail": "", "paths": []}

    if not workdir or not isinstance(workdir, str):
        out["detail"] = "no-workdir"
        return out
    if not isinstance(diff, str) or not diff.strip():
        out["detail"] = "malformed-fix"
        return out

    check = subprocess.run(
        ["git", "-C", workdir, "apply", "--check", "--whitespace=nowarn"],
        input=diff, text=True, capture_output=True,
    )
    if check.returncode != 0:
        out["detail"] = "apply-failed"
        return out

    applied = subprocess.run(
        ["git", "-C", workdir, "apply", "--whitespace=nowarn"],
        input=diff, text=True, capture_output=True,
    )
    if applied.returncode != 0:
        # --check passed but the real apply failed -- still a skip, never a
        # partial write (a single `git apply` invocation is all-or-nothing).
        out["detail"] = "apply-failed"
        return out

    out["status"] = "applied"
    out["paths"] = _diff_paths(diff)
    return out


def apply_all(workdir, fixes):
    results = []
    for fix in fixes or []:
        if isinstance(fix, dict):
            results.append(apply_fix(workdir, fix))
    return results

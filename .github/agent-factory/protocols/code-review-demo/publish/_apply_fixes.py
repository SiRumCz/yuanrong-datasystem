#!/usr/bin/env python3
"""Pure patch-applier for the demo fix phase. No git, no network — only file edits.

apply_fix replaces the 1-based `line` in `<workdir>/<path>` with `suggested_patch`
(possibly multiline). If `original_line` is present it must match the current line
(trailing newline ignored), else the fix is skipped as drift. apply_all maps over
a list of fixes and returns one result dict each.
"""
import os


def apply_fix(workdir, fix):
    cid = fix.get("cluster_id")
    rel = fix.get("path") or ""
    line = fix.get("line")
    patch = fix.get("suggested_patch")
    out = {"cluster_id": cid, "path": rel, "status": "skipped", "detail": ""}

    if not isinstance(rel, str) or not rel or not isinstance(line, int) or line < 1 \
            or not isinstance(patch, str):
        out["detail"] = "malformed-fix"
        return out

    target = os.path.join(workdir, rel)
    if not os.path.isfile(target):
        out["detail"] = "missing-file"
        return out

    try:
        with open(target) as fh:
            lines = fh.readlines()  # each retains its "\n"
    except OSError:
        out["detail"] = "io-error"
        return out

    if line > len(lines):
        out["detail"] = "line-out-of-range"
        return out

    current = lines[line - 1].rstrip("\n")
    expected = fix.get("original_line")
    if expected is not None and current != expected.rstrip("\n"):
        out["detail"] = "drift"
        return out

    trailing_nl = lines[line - 1].endswith("\n")
    replacement = patch.split("\n")
    new_block = [seg + "\n" for seg in replacement]
    if not trailing_nl:
        new_block[-1] = new_block[-1].rstrip("\n")
    lines[line - 1:line] = new_block
    try:
        with open(target, "w") as fh:
            fh.writelines(lines)
    except OSError:
        out["detail"] = "write-error"
        return out

    out["status"] = "applied"
    return out


def apply_all(workdir, fixes):
    """Apply fixes in order; a multiline patch shifts later same-file line numbers, so a now-mismatched original_line causes the drift guard to skip rather than corrupt."""
    results = []
    for fix in fixes or []:
        if isinstance(fix, dict):
            results.append(apply_fix(workdir, fix))
    return results

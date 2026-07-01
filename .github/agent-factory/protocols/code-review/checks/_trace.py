#!/usr/bin/env python3
"""Diff-anchor helpers shared by traces-exist-in-diff and judge-coverage."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diff  # noqa: E402

parse_diff = _diff.parse_diff
norm = _diff.norm


def verify_finding(f, fmap, path, cat):
    """Return an error string if the finding's anchor is invalid, else None.
    (Moved verbatim from traces-exist-in-diff.py.)"""
    if not isinstance(f, dict):
        return f"malformed finding ({cat} × {path})"
    side = f.get("side")
    if side not in ("RIGHT", "LEFT"):
        return f"finding side must be RIGHT or LEFT ({cat} × {path}): {side!r}"
    smap = fmap.get(side, {})
    line = f.get("line")
    start = f.get("start_line")
    if not isinstance(line, int) or line not in smap:
        return f"line {line} not on {side} side of {path}'s diff ({cat})"
    if start is not None:
        if not isinstance(start, int) or start not in smap:
            return f"start_line {start} not on {side} side of {path}'s diff ({cat})"
        if start >= line:
            return f"start_line {start} must be < line {line} ({cat} × {path})"
        hunk = smap[line][1]
        for n in range(start, line + 1):
            if n not in smap or smap[n][1] != hunk:
                return (f"lines {start}-{line} are not one contiguous hunk on "
                        f"{side} ({cat} × {path})")
        lines = [smap[n][0] for n in range(start, line + 1)]
    else:
        lines = [smap[line][0]]
    got = norm("\n".join(lines))
    want = norm(f.get("existing_code") or "")
    if got != want:
        anchor = f"{start}-{line}" if start is not None else f"{line}"
        return (f"existing_code does not match {side} line(s) {anchor} of "
                f"{path} ({cat})")
    return None


def findings_anchor_errors(evidence, diff_path):
    """Walk evidence.files[].verdicts[].findings[] + examined and return the list
    of anchor/examined errors against the diff. (Moved verbatim from
    traces-exist-in-diff.py's main loop.)"""
    maps = parse_diff(diff_path)
    bad = []
    files = evidence.get("files", []) if isinstance(evidence, dict) else []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        fmap = maps.get(path, {"RIGHT": {}, "LEFT": {}})
        blob = "\n".join(c for (c, _h) in list(fmap["RIGHT"].values()) + list(fmap["LEFT"].values()))
        for verdict in (entry.get("verdicts") or []):
            if not isinstance(verdict, dict):
                continue
            cat = verdict.get("category")
            for f in (verdict.get("findings") or []):
                err = verify_finding(f, fmap, path, cat)
                if err:
                    bad.append(err)
            for ident in (verdict.get("examined") or []):
                if ident not in blob:
                    bad.append(f"examined identifier not in {path}'s diff ({cat}): {ident!r}")
    return bad

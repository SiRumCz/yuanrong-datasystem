#!/usr/bin/env python3
"""Pure patch-applier for the demo fix phase. No git, no network — only file edits.

Safety model: a fix MUST carry `original_line` (the exact current content of the
target line). The applier verifies it and NEVER trusts the agent's line number
blindly — if the agent's `line` doesn't match `original_line`, it RE-ANCHORS by
searching the file for that exact content:
  - exactly one match  -> apply there (detail "reanchored")
  - zero matches       -> skip (detail "not-found")   [wrong/hallucinated line]
  - many matches       -> skip (detail "ambiguous")    [can't disambiguate]
A fix without `original_line` is skipped (detail "no-original") — we refuse to
edit a line we cannot verify. This prevents corrupting an unrelated line when the
LLM emits a wrong line number.

apply_all maps over a list of fixes and returns one result dict each. Fixes apply
in order, so a multiline patch shifts later same-file line numbers; the
re-anchor/verify step then skips a now-mismatched fix rather than corrupting.
"""
import os


def apply_fix(workdir, fix):
    cid = fix.get("cluster_id")
    rel = fix.get("path") or ""
    line = fix.get("line")
    patch = fix.get("suggested_patch")
    expected = fix.get("original_line")
    out = {"cluster_id": cid, "path": rel, "status": "skipped", "detail": "", "applied_line": None}

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

    # Require verifiable content — never edit a line we can't confirm.
    if not isinstance(expected, str) or expected == "":
        out["detail"] = "no-original"
        return out
    exp = expected.rstrip("\n")

    # Locate the line to edit: trust the agent's number only if it matches;
    # otherwise re-anchor by exact content.
    if 1 <= line <= len(lines) and lines[line - 1].rstrip("\n") == exp:
        idx = line - 1
    else:
        matches = [i for i, l in enumerate(lines) if l.rstrip("\n") == exp]
        if len(matches) == 1:
            idx = matches[0]
            out["detail"] = "reanchored"
        elif not matches:
            out["detail"] = "not-found"
            return out
        else:
            out["detail"] = "ambiguous"
            return out

    trailing_nl = lines[idx].endswith("\n")
    new_block = [seg + "\n" for seg in patch.split("\n")]
    if not trailing_nl:
        new_block[-1] = new_block[-1].rstrip("\n")
    lines[idx:idx + 1] = new_block
    try:
        with open(target, "w") as fh:
            fh.writelines(lines)
    except OSError:
        out["detail"] = "write-error"
        return out

    out["status"] = "applied"
    out["applied_line"] = idx + 1
    return out


def apply_all(workdir, fixes):
    results = []
    for fix in fixes or []:
        if isinstance(fix, dict):
            results.append(apply_fix(workdir, fix))
    return results

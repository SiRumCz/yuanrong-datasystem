#!/usr/bin/env python3
"""Check: every finding's anchor (line[/start_line] on a side) resolves to the
claimed snippet in the independently-fetched diff, and every `examined`
identifier appears in that file's diff hunks.

Usage: traces-exist-in-diff.py <evidence.json> <diff.txt> <changed-files.txt>

This replaces the former "snippet appears somewhere in the diff" check: a finding
must now name the exact line(s) it critiques (RIGHT = new-file line numbers,
LEFT = old-file line numbers), and we verify the snippet sits there. Anchors that
pass here are valid GitHub review positions, so the publish hook can post them in
a single review without the all-or-nothing reviews API 422-ing.
"""
import json
import re
import sys

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def norm(s: str) -> str:
    """Collapse all runs of whitespace to single spaces (matches the old check)."""
    return " ".join(s.split())


def parse_diff(path):
    """Return {file: {"RIGHT": {lineno: (content, hunk_id)}, "LEFT": {...}}}.

    Context lines populate both sides; '+' only RIGHT; '-' only LEFT. Each mapped
    line records the id of the hunk it belongs to (for same-hunk range checks).
    """
    maps = {}
    cur = None
    minus_path = None
    in_hunk = False
    right_no = left_no = 0
    hunk_id = -1
    with open(path) as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith("diff --git"):
                cur, in_hunk = None, False
                minus_path = None
                continue
            if line.startswith("--- "):
                minus = line[4:]
                if minus == "/dev/null":
                    minus_path = None
                elif minus.startswith("a/"):
                    minus_path = minus[2:]
                else:
                    minus_path = minus
                in_hunk = False
                continue
            if line.startswith("+++ "):
                plus = line[4:]
                if plus == "/dev/null":
                    cur = minus_path  # deleted file: key it under its old path
                elif plus.startswith("b/"):
                    cur = plus[2:]
                else:
                    cur = plus
                if cur is not None:
                    maps.setdefault(cur, {"RIGHT": {}, "LEFT": {}})
                in_hunk = False
                continue
            m = HUNK_RE.match(line)
            if m:
                left_no, right_no = int(m.group(1)), int(m.group(2))
                hunk_id += 1
                in_hunk = True
                continue
            if not in_hunk or cur is None or line == "":
                continue
            tag, content = line[0], line[1:]
            if tag == " ":
                maps[cur]["LEFT"][left_no] = (content, hunk_id)
                maps[cur]["RIGHT"][right_no] = (content, hunk_id)
                left_no += 1
                right_no += 1
            elif tag == "+":
                maps[cur]["RIGHT"][right_no] = (content, hunk_id)
                right_no += 1
            elif tag == "-":
                maps[cur]["LEFT"][left_no] = (content, hunk_id)
                left_no += 1
            # "\ No newline at end of file" and any other marker: ignore
    return maps


def verify_finding(f, fmap, path, cat):
    """Return an error string if the finding's anchor is invalid, else None."""
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


def main():
    if len(sys.argv) < 4:
        print(json.dumps({
            "check": "traces-exist-in-diff",
            "pass": False,
            "feedback": "usage: traces-exist-in-diff.py <evidence.json> <diff.txt> <changed-files.txt>",
        }))
        sys.exit(0)
    # _files (changed-files.txt) is unused: the diff is the source of truth here.
    ev_path, diff_path, _files = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        with open(ev_path) as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        evidence = {}
    maps = parse_diff(diff_path)

    bad = []
    files = evidence.get("files", []) if isinstance(evidence, dict) else []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        fmap = maps.get(path, {"RIGHT": {}, "LEFT": {}})
        blob = "\n".join(
            c for (c, _h) in list(fmap["RIGHT"].values()) + list(fmap["LEFT"].values())
        )
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
                    bad.append(
                        f"examined identifier not in {path}'s diff ({cat}): {ident!r}"
                    )

    if bad:
        out = {
            "check": "traces-exist-in-diff",
            "pass": False,
            "feedback": "Unverifiable claims: " + "; ".join(bad),
        }
    else:
        out = {"check": "traces-exist-in-diff", "pass": True, "feedback": ""}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()

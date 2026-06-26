#!/usr/bin/env python3
"""Shared unified-diff parser: RIGHT/LEFT line maps per file."""
import re

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def norm(s: str) -> str:
    """Collapse all runs of whitespace to single spaces."""
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

#!/usr/bin/env python3
"""anchored-to-real-code (iterate) — every location's existing_code verbatim-
matches the file at path[start_line:end_line], read from SCAN_ROOT (the checked-
out scanned ref). Trailing-whitespace-insensitive per line. This is the anti-
hallucination gate: duplication is a CHECKED claim, not trusted prose.
Usage: <ev.json> <diff> <changed-files>; exits 0. Empty patterns -> pass."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

CHECK = "anchored-to-real-code"


def _norm(text):
    return [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]


def main():
    try:
        _run()
    except Exception as e:  # belt-and-suspenders: the ABI requires exit 0 + one JSON verdict, always
        _common.emit(CHECK, False, f"check crashed: {e}")


def _run():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    ev = _common.load_evidence(ev_path)
    if ev is None:
        _common.emit(CHECK, False, "evidence.json is missing or not valid JSON object")
        return
    root = os.environ.get("SCAN_ROOT", ".")
    root_real = os.path.realpath(root)
    cache = {}

    def is_confined(path):
        target_real = os.path.realpath(os.path.join(root, path))
        return target_real == root_real or target_real.startswith(root_real + os.sep)

    def read_lines(path):
        if path not in cache:
            try:
                with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
                    cache[path] = fh.read().split("\n")
            except OSError:
                cache[path] = None
        return cache[path]

    patterns = ev.get("patterns", [])
    if not isinstance(patterns, list):
        _common.emit(CHECK, False, "`patterns` is not an array")
        return

    for pat in patterns:
        if not isinstance(pat, dict):
            _common.emit(CHECK, False, "a `patterns` entry is not an object")
            return
        pid = pat.get("id", "?")
        locs = pat.get("locations", [])
        if not isinstance(locs, list):
            _common.emit(CHECK, False, f"[{pid}] `locations` is not an array")
            return
        for loc in locs:
            if not isinstance(loc, dict):
                _common.emit(CHECK, False, f"[{pid}] a location entry is not an object")
                return
            path = loc.get("path", "")
            if not isinstance(path, str) or not path:
                _common.emit(CHECK, False, f"[{pid}] location has missing/invalid path")
                return
            if not is_confined(path):
                _common.emit(CHECK, False, f"[{pid}] path '{path}' escapes the scanned root")
                return
            s, e = loc.get("start_line"), loc.get("end_line")
            lines = read_lines(path)
            if lines is None:
                _common.emit(CHECK, False, f"[{pid}] cannot read file '{path}' at scanned ref")
                return
            if (
                not isinstance(s, int) or isinstance(s, bool)
                or not isinstance(e, int) or isinstance(e, bool)
                or s < 1 or e > len(lines) or e < s
            ):
                _common.emit(CHECK, False, f"[{pid}] '{path}' lines {s}-{e} out of range (file has {len(lines)} lines)")
                return
            actual = _norm("\n".join(lines[s - 1:e]))
            claimed_code = loc.get("existing_code", "")
            if not isinstance(claimed_code, str):
                _common.emit(CHECK, False, f"[{pid}] '{path}' existing_code is not a string")
                return
            claimed = _norm(claimed_code)
            if actual != claimed:
                _common.emit(CHECK, False,
                             f"[{pid}] existing_code does not match '{path}' lines {s}-{e} "
                             f"(claimed {len(claimed)} lines, file span {len(actual)} lines)")
                return
    _common.emit(CHECK, True, "")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""evidence-wellformed (iterate) — the detect evidence is structurally valid:
object; non-empty `scanned`; `patterns` an array; each pattern has id/name/
rationale, a severity in the configured enum, and >= min_locations locations,
each with path + positive start<=end lines + non-empty existing_code.
Usage: <ev.json> <diff> <changed-files>; exits 0."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

CHECK = "evidence-wellformed"


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
    p = _common.params()
    try:
        min_loc = int(p.get("min_locations", 2))
    except (TypeError, ValueError):
        min_loc = 2
    try:
        severities = set(p.get("severities", ["high", "medium", "low"]))
    except TypeError:
        severities = {"high", "medium", "low"}

    scanned = ev.get("scanned")
    if not isinstance(scanned, list) or not scanned or not all(isinstance(s, str) for s in scanned):
        _common.emit(CHECK, False, "`scanned` must be a non-empty array of strings (proves the code was read)")
        return

    patterns = ev.get("patterns")
    if not isinstance(patterns, list):
        _common.emit(CHECK, False, "`patterns` must be an array")
        return

    for i, pat in enumerate(patterns):
        tag = f"patterns[{i}]"
        if not isinstance(pat, dict):
            _common.emit(CHECK, False, f"{tag} is not an object")
            return
        for key in ("id", "name", "rationale"):
            if not _common.non_trivial(pat.get(key)):
                _common.emit(CHECK, False, f"{tag}.{key} missing or trivial")
                return
        sev = pat.get("severity")
        if not isinstance(sev, str) or sev not in severities:
            _common.emit(CHECK, False, f"{tag}.severity '{sev}' not in {sorted(severities)}")
            return
        locs = pat.get("locations")
        if not isinstance(locs, list) or len(locs) < min_loc:
            _common.emit(CHECK, False, f"{tag} needs >= {min_loc} locations, got {len(locs) if isinstance(locs, list) else 0}")
            return
        for j, loc in enumerate(locs):
            lt = f"{tag}.locations[{j}]"
            if not isinstance(loc, dict) or not _common.non_trivial(loc.get("path")):
                _common.emit(CHECK, False, f"{lt}.path missing")
                return
            s, e = loc.get("start_line"), loc.get("end_line")
            if (
                not isinstance(s, int) or isinstance(s, bool)
                or not isinstance(e, int) or isinstance(e, bool)
                or s < 1 or e < s
            ):
                _common.emit(CHECK, False, f"{lt} needs integer start_line>=1 and end_line>=start_line")
                return
            if not _common.non_trivial(loc.get("existing_code")):
                _common.emit(CHECK, False, f"{lt}.existing_code missing (need verbatim anchor)")
                return
    _common.emit(CHECK, True, "")


if __name__ == "__main__":
    main()

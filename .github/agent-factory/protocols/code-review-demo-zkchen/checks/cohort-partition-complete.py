#!/usr/bin/env python3
"""Check: the agent's cohorts form a COMPLETE PARTITION of the PR's changed files —
every changed file belongs to exactly one cohort. Custody's guided-overview workflow
only prompt-asserts this; the engine receives the independent changed-files list at
check time (argv[3], from `gh pr diff --name-only`), so we enforce it deterministically.

A gap (changed file in no cohort) or overlap (file claimed by >1 cohort) corrupts the
downstream diffusion inputs (NS/ND/NF) that the risk scorer derives from cohort files,
so this is an iterate-severity defect: re-dispatch the agent with the offending paths.

Usage: cohort-partition-complete.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        _emit([f"evidence unreadable/not JSON: {exc}"])
        return

    cohorts = ev.get("cohorts") if isinstance(ev, dict) else None
    if not isinstance(cohorts, list):
        _emit(["`cohorts` is missing or not an array"])
        return

    # Per-file assignment count across all cohorts.
    counts = {}
    for c in cohorts:
        if not isinstance(c, dict):
            continue
        for fn in (c.get("files") or []):
            if isinstance(fn, str):
                counts[fn] = counts.get(fn, 0) + 1
    union = set(counts)
    overlap = sorted(f for f, n in counts.items() if n > 1)

    changed = set(_paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else ""))

    problems = []
    if overlap:
        problems.append(f"{len(overlap)} file(s) in >1 cohort: " + ", ".join(overlap[:5]))

    if changed:
        gap = sorted(changed - union)
        extra = sorted(union - changed)
        if gap:
            problems.append(f"{len(gap)} changed file(s) in no cohort: " + ", ".join(gap[:5]))
        if extra:
            problems.append(f"{len(extra)} cohort file(s) not in the diff: " + ", ".join(extra[:5]))
    elif not overlap:
        # No ground-truth changed-files list available (e.g. offline) — we can only
        # verify intra-evidence overlap, which passed. Don't fail on unverifiable coverage.
        print(json.dumps({"check": "cohort-partition-complete", "pass": True,
                          "feedback": "no changed-files list available; verified no cohort overlap only"}))
        return

    _emit(problems)


def _emit(problems):
    if problems:
        print(json.dumps({"check": "cohort-partition-complete", "pass": False,
                          "feedback": "cohort partition incomplete: " + "; ".join(problems[:4])}))
    else:
        print(json.dumps({"check": "cohort-partition-complete", "pass": True, "feedback": ""}))


if __name__ == "__main__":
    main()

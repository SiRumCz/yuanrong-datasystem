#!/usr/bin/env python3
"""Check: fix evidence shape and internal consistency."""
import json
import sys


def _non_empty_str(v):
    return isinstance(v, str) and bool(v)


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        _emit([f"evidence unreadable/not JSON: {exc}"])
        return
    if not isinstance(ev, dict):
        _emit(["evidence is not a JSON object"])
        return

    p = []
    if ev.get("mode") != "edit":
        p.append("mode must be 'edit'")

    fixes = ev.get("fixes")
    if not isinstance(fixes, list):
        p.append("`fixes` must be an array")
        fixes = []
    skipped = ev.get("skipped") or []
    if not isinstance(skipped, list):
        p.append("`skipped` must be an array when present")
        skipped = []

    fixed_ids = []
    skipped_ids = []
    for i, fix in enumerate(fixes):
        fp = f"fixes[{i}]"
        if not isinstance(fix, dict):
            p.append(f"{fp} is not an object")
            continue
        if not _non_empty_str(fix.get("cluster_id")):
            p.append(f"{fp}.cluster_id missing/empty")
        else:
            fixed_ids.append(fix.get("cluster_id"))
        if not _non_empty_str(fix.get("diff")):
            p.append(f"{fp}.diff missing/empty")
        if len(p) > 8:
            break

    for i, skip in enumerate(skipped):
        sp = f"skipped[{i}]"
        if not isinstance(skip, dict):
            p.append(f"{sp} is not an object")
            continue
        if not _non_empty_str(skip.get("cluster_id")):
            p.append(f"{sp}.cluster_id missing/empty")
        else:
            skipped_ids.append(skip.get("cluster_id"))
        if not _non_empty_str(skip.get("reason")):
            p.append(f"{sp}.reason missing/empty")
        if len(p) > 8:
            break

    both = sorted(set(fixed_ids).intersection(skipped_ids))
    if both:
        p.append(f"cluster_id(s) in both fixes and skipped: {', '.join(both)}")
    if len(fixed_ids) != len(set(fixed_ids)):
        p.append("duplicate cluster_id in fixes")
    if len(skipped_ids) != len(set(skipped_ids)):
        p.append("duplicate cluster_id in skipped")

    _emit(p)


def _emit(problems):
    if problems:
        print(
            json.dumps(
                {
                    "check": "fix-schema-valid",
                    "pass": False,
                    "feedback": "fix schema invalid: " + "; ".join(problems[:6]),
                }
            )
        )
    else:
        print(json.dumps({"check": "fix-schema-valid", "pass": True, "feedback": ""}))


if __name__ == "__main__":
    main()

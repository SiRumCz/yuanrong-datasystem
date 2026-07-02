#!/usr/bin/env python3
"""Check: review evidence shape, enums, and branch-dimension consistency."""
import json
import os
import sys

DIM = {"correctness", "test", "performance", "security", "maintainability"}
SEV = {"critical", "high", "medium", "low"}
VERDICT = {"APPROVE", "COMMENT", "REQUEST_CHANGES"}


def _non_empty_str(v):
    return isinstance(v, str) and bool(v)


def _pos_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def main():
    try:
        params = json.loads(os.environ.get("CHECK_PARAMS", "") or "{}")
        if not isinstance(params, dict):
            params = {}
    except ValueError:
        params = {}
    expected_dim = params.get("dimension")

    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        _emit([f"evidence unreadable/not JSON: {exc}"])
        return

    p = []
    if not isinstance(ev, dict):
        _emit(["evidence is not a JSON object"])
        return

    dim = ev.get("dimension")
    verdict = ev.get("verdict")
    findings = ev.get("findings")
    if dim not in DIM:
        p.append(f"`dimension` {dim!r} not in {sorted(DIM)}")
    if expected_dim not in DIM:
        p.append("CHECK_PARAMS.dimension missing or invalid")
    elif dim != expected_dim:
        p.append(f"`dimension` {dim!r} does not match branch {expected_dim!r}")
    if verdict not in VERDICT:
        p.append(f"`verdict` {verdict!r} not in {sorted(VERDICT)}")
    if not isinstance(findings, list):
        p.append("`findings` must be an array")
        findings = []

    has_high_or_critical = False
    for i, f in enumerate(findings):
        fp = f"findings[{i}]"
        if not isinstance(f, dict):
            p.append(f"{fp} is not an object")
            continue
        if not _non_empty_str(f.get("path")):
            p.append(f"{fp}.path missing/empty")
        if not _pos_int(f.get("line")):
            p.append(f"{fp}.line must be an integer >= 1")
        sev = f.get("severity")
        if sev not in SEV:
            p.append(f"{fp}.severity {sev!r} not in {sorted(SEV)}")
        else:
            has_high_or_critical = has_high_or_critical or sev in {"critical", "high"}
        cat = f.get("category")
        if cat not in DIM:
            p.append(f"{fp}.category {cat!r} not in {sorted(DIM)}")
        elif cat != dim:
            p.append(f"{fp}.category {cat!r} does not match dimension {dim!r}")
        for key in ("title", "impact", "fix"):
            if not _non_empty_str(f.get(key)):
                p.append(f"{fp}.{key} missing/empty")
        if f.get("start_line") is not None:
            if not _pos_int(f.get("start_line")):
                p.append(f"{fp}.start_line must be an integer >= 1")
            elif _pos_int(f.get("line")) and f.get("start_line") > f.get("line"):
                p.append(f"{fp}.start_line must be <= line")
        if len(p) > 8:
            break

    if verdict == "APPROVE" and findings:
        p.append("APPROVE evidence must have no findings")
    if has_high_or_critical and verdict != "REQUEST_CHANGES":
        p.append("critical/high findings require verdict REQUEST_CHANGES")

    _emit(p)


def _emit(problems):
    if problems:
        print(
            json.dumps(
                {
                    "check": "review-schema-valid",
                    "pass": False,
                    "feedback": "review schema invalid: " + "; ".join(problems[:6]),
                }
            )
        )
    else:
        print(json.dumps({"check": "review-schema-valid", "pass": True, "feedback": ""}))


if __name__ == "__main__":
    main()

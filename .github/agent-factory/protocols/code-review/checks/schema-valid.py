#!/usr/bin/env python3
import json, os, sys

def emit(ok, feedback):
    print(json.dumps({"check": "schema-valid", "pass": ok, "feedback": feedback}))
    sys.exit(0)

def main():
    ev_path = sys.argv[1]
    try:
        with open(ev_path) as f: ev = json.load(f)
    except Exception:
        emit(False, "evidence file is missing or not valid JSON")
    if not isinstance(ev.get("files"), list):
        emit(False, "top-level .files array is missing")
    for fe in ev["files"]:
        if not (isinstance(fe, dict) and isinstance(fe.get("verdicts"), list)):
            emit(False, "a .files entry is not an object with a verdicts array; "
                        "check that every file is an object and verdicts is an array")
    try:
        params = json.loads(os.environ.get("CHECK_PARAMS", "") or "{}")
    except Exception:
        params = {}
    cats = (params or {}).get("categories")  # CHECK_PARAMS="null" -> json None; stay exit-0
    if not (isinstance(cats, list) and len(cats) > 0):
        emit(False, "schema-valid: no categories in CHECK_PARAMS "
                    "(engine must pass params.categories for this check's node)")
    errs = []
    for fe in ev["files"]:
        p = fe.get("path")
        for v in fe.get("verdicts", []):
            c = v.get("category"); verdict = v.get("verdict")
            findings = v.get("findings") or []
            if c not in cats:
                errs.append(f"illegal category {c} in {p}")
            elif verdict not in ("issues-found", "none-found"):
                errs.append(f"illegal verdict {verdict} for {c} × {p}")
            elif verdict == "issues-found" and len(findings) == 0:
                errs.append(f"issues-found with no findings: {c} × {p}")
            elif verdict == "issues-found" and not all(
                    len(fd.get("existing_code") or "") > 0 and len(fd.get("comment") or "") > 0
                    for fd in findings):
                errs.append(f"finding with empty existing_code or comment: {c} × {p}")
            elif verdict == "issues-found" and not all(
                    fd.get("side") in ("RIGHT", "LEFT")
                    and isinstance(fd.get("line"), int) and not isinstance(fd.get("line"), bool) and fd.get("line") >= 1
                    and (fd.get("start_line") is None
                         or (isinstance(fd.get("start_line"), int) and not isinstance(fd.get("start_line"), bool) and fd.get("start_line") >= 1))
                    for fd in findings):
                errs.append(f"finding missing valid line/side anchor: {c} × {p}")
            elif verdict == "none-found" and len(v.get("examined") or []) == 0:
                errs.append(f"none-found with no examined identifiers: {c} × {p}")
    emit(len(errs) == 0, "; ".join(errs))

if __name__ == "__main__":
    main()

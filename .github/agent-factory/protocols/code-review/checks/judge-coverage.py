#!/usr/bin/env python3
"""Form-check for a <leg>-judge: re-runs the leg's gather check on the verbatim
`evidence.gather` copy (verifying scope/verdict/coverage/traceability in one call),
then requires a valid `severity` grade for every gather finding. Per-leg dispatch
via CHECK_PARAMS {"leg","mode"}. Zone 3 — re-derives ground truth, holds no creds.
ABI: judge-coverage.py <evidence.json> <diff.txt> <changed-files.txt>"""
import importlib.util, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _paths  # noqa: E402
import _coherence  # noqa: E402

NAME = "judge-coverage"
SEVS = {"blocking", "advisory", "noise"}

def _emit(ok, fb):
    print(json.dumps({"check": NAME, "pass": ok, "feedback": fb}));

def _load(stem):
    spec = importlib.util.spec_from_file_location(stem.replace("-", "_"), os.path.join(HERE, f"{stem}.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def _gather_refs(mode, gather):
    """The finding refs the judge must grade, per leg."""
    if mode == "coherence":
        return _coherence.finding_refs(gather)
    if mode == "plan-spec":
        return [c.get("requirement") for c in gather.get("spec_to_plan", []) if isinstance(c, dict)] + \
               [c.get("plan_item") for c in gather.get("plan_to_spec", []) if isinstance(c, dict)]
    if mode == "code-plan":
        return [c.get("plan_item") for c in gather.get("plan_to_code", []) if isinstance(c, dict)]
    if mode == "spec-solves":
        return [c.get("problem") for c in gather.get("matrix", []) if isinstance(c, dict)]
    if mode == "mm":
        return [str(i) for i, _ in enumerate(gather.get("divergences", []))]
    if mode == "security":
        return [str(i) for i, _ in enumerate(gather.get("engine_report", {}).get("violations", []))]
    return []

def main():
    try:
        params = json.loads(os.environ.get("CHECK_PARAMS", "") or "{}")
        mode = params.get("mode"); leg = params.get("leg")
    except ValueError:
        mode = leg = None
    if not mode or not leg:
        _emit(False, "CHECK_PARAMS must carry {leg, mode}"); return
    try:
        ev = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else {}
    except (OSError, ValueError) as exc:
        _emit(False, f"evidence unreadable: {exc}"); return
    if not isinstance(ev, dict) or not isinstance(ev.get("gather"), dict):
        _emit(False, "judge evidence needs a `gather` object"); return
    gather = ev["gather"]
    diff_text = open(sys.argv[2]).read() if len(sys.argv) > 2 else ""
    files = _paths.read_changed_files(sys.argv[3] if len(sys.argv) > 3 else "")
    body = os.environ.get("PR_BODY", "") or ""; repo = os.environ.get("GITHUB_REPOSITORY", ""); pr = os.environ.get("PR", "")

    # 1) re-run the leg's own gather check on the copied gather evidence
    if mode == "plan-spec":
        ok, fb = _load("plan-spec-coverage").evaluate(gather, diff_text, files, body=body, repo=repo, pr=pr)
    elif mode == "code-plan":
        ok, fb = _load("code-plan-coverage").evaluate(gather, diff_text, files, body=body, repo=repo, pr=pr)
        if ok:  # also re-verify the copied diff anchors (code-plan-coverage doesn't)
            import _trace  # noqa: E402
            errs = _trace.findings_anchor_errors(gather, sys.argv[2] if len(sys.argv) > 2 else "")
            if errs:
                ok, fb = False, "code findings anchors: " + "; ".join(errs[:3])
    elif mode == "spec-solves":
        ok, fb = _load("spec-solves-issue-coverage").evaluate(gather, diff_text, files, body=body, repo=repo, pr=pr)
    elif mode == "coherence":
        is_doc = leg.startswith("docs"); kind = _paths.is_doc if is_doc else _paths.is_test
        r = _coherence.evaluate("coherence", gather, files, is_kind=kind,
                                kind_label="doc" if is_doc else "test",
                                applicable_without_code=is_doc)
        ok, fb = r["pass"], r["feedback"]
    elif mode == "mm":
        v = gather.get("verdict")
        ok = v in ("compliant", "diverges"); fb = "ok" if ok else f"mm verdict not in enum: {v!r}"
    elif mode == "security":
        sgc = _load("security-gather-coverage")
        # Re-run required sub-object presence checks
        for key in ("cedar", "guardians", "engine_report"):
            val = gather.get(key)
            if val is None:
                _emit(False, f"gather copy fails its own check: missing required field: {key!r}"); return
            if not isinstance(val, dict):
                _emit(False, f"gather copy fails its own check: {key!r} must be a JSON object"); return
        v = gather.get("verdict")
        if v not in sgc.VALID_VERDICTS:
            _emit(False, f"gather copy fails its own check: verdict {v!r} not in allowed enum {sorted(sgc.VALID_VERDICTS)}"); return
        recomputed = sgc._recompute_verdict(gather.get("engine_report", {}))
        if v != recomputed:
            ok = False; fb = f"verdict mismatch: evidence says {v!r} but recompute gives {recomputed!r}"
        else:
            ok = True; fb = f"security-gather form valid: verdict={v!r}"
    else:
        _emit(False, f"unknown mode {mode!r}"); return
    if not ok:
        _emit(False, f"gather copy fails its own check: {fb}"); return

    # 2) every gather finding must carry exactly one valid severity grade
    graded = ev.get("graded_findings")
    if not isinstance(graded, list):
        _emit(False, "graded_findings must be an array"); return
    for g in graded:
        if not isinstance(g, dict) or g.get("severity") not in SEVS or not g.get("ref"):
            _emit(False, "each graded finding needs {ref, severity in blocking|advisory|noise}"); return
    refs_needed = [r for r in _gather_refs(mode, gather) if r is not None]
    graded_refs = {g["ref"] for g in graded}
    missing = [r for r in refs_needed if str(r) not in graded_refs and r not in graded_refs]
    if missing:
        _emit(False, f"findings not graded: {missing[:5]}"); return
    _emit(True, f"{leg}: gather re-verified + {len(graded)} findings graded.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Conclude hook for the `overview` phase. Computes the AUTHORITATIVE breaking-change
risk band/score deterministically from the agent's evidence (cohorts + per-finding
severityClass), exactly as custody's assemble.js runs score.js downstream of the
guided-overview agent — the agent never owns the band. Fail-loud: unreadable/garbled
evidence yields band 'unknown' (never a silent Low) and blocks the pipeline.

Per the parity decision, a successfully-scored band — including Critical — does NOT
block (risk is advisory triage, matching custody, where only an errored run blocks
downstream review). The agent's own `risk_band` is kept as an advisory hint and is
flagged when it disagrees with the computed band.

ABI (4.0.0): conclude-overview.py <evidence.json> <instance-key>. The EXIT CODE is
the contract — 0 = content, nonzero = this step objects; stdout is the check-run
summary text. Whether objecting also STOPS the run is the node's own
`on_blocked: "halt"` in protocol.json (overview declares it), not this hook's
business. Also writes a custody-shaped overview.json (summary + overall + scored
cohorts + meta) to $OVERVIEW_OUT for downstream/dashboard use.

The engine's block-severity signal is deliberately NOT read here: `lib.decide`
folds a failed `on_fail: "block"` check into this node's exit code itself
(`lib.block_exit`), so re-reading it would double-count one fact. This hook owns
exactly one judgment — whether the risk band could be SCORED at all.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _risk_score as rs  # noqa: E402


def _load_evidence(path):
    """Return (evidence_dict_or_None, fatal_reason_or_None). fatal => band 'unknown'."""
    try:
        with open(path) as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, f"evidence unreadable / not JSON: {exc}"
    if not isinstance(ev, dict):
        return None, "evidence is not a JSON object"
    if ev.get("error"):
        return None, str(ev.get("error"))
    if not isinstance(ev.get("cohorts"), list):
        # A missing/garbled cohorts list is the engine's "agent produced nothing"
        # fallback ({"files":[]}) — fail loud rather than score it as Low.
        return None, "evidence has no `cohorts` list (agent produced no overview)"
    return ev, None


def _file_stats():
    """Per-file {additions,deletions} keyed by path, mirroring custody assemble.js
    fileStatsFrom(pr). Sources, in order: $OVERVIEW_PR_JSON (tests/CI prefetch), a
    staged /tmp/agent/pr.json, or a best-effort `gh pr view`. Absent => {} (the scorer
    then defaults files to 0/0, custody's documented missing-fileStats tolerance — the
    band is unaffected; only the score's size term degrades)."""
    pr = None
    for path in (os.environ.get("OVERVIEW_PR_JSON"), "/tmp/agent/pr.json"):
        if path and os.path.isfile(path):
            try:
                with open(path) as fh:
                    pr = json.load(fh)
                break
            except (OSError, ValueError):
                pr = None
    if pr is None and os.environ.get("ENGINE_LOCAL", "0") != "1":
        prn = os.environ.get("PR", "")
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if prn and repo:
            try:
                out = subprocess.run(
                    ["gh", "pr", "view", prn, "--repo", repo, "--json", "files"],
                    text=True, capture_output=True, timeout=30,
                )
                if out.returncode == 0:
                    pr = json.loads(out.stdout or "{}")
            except (OSError, ValueError, subprocess.SubprocessError):
                pr = None
    stats = {}
    for f in ((pr or {}).get("files") or []):
        name = f.get("path") or f.get("filename")
        if name:
            stats[name] = {"additions": f.get("additions") or 0,
                           "deletions": f.get("deletions") or 0}
    return stats


def _attach_layers(scored_cohorts, input_cohorts):
    """score.js drops layers; reattach by (cohortOrder, cohort) identity (assemble.js)."""
    by_key = {}
    for c in (input_cohorts or []):
        by_key[(c.get("cohortOrder"), c.get("cohort"))] = c
    out = []
    for sc in scored_cohorts:
        src = by_key.get((sc.get("cohortOrder"), sc.get("cohort"))) or {}
        out.append({**sc, "layers": src.get("layers") or []})
    return out


def _meta(instance, file_stats_keys):
    head = os.environ.get("HEAD_SHA") or os.environ.get("PR_HEAD_SHA", "")
    meta = {"head_sha": head}
    if instance.startswith("pr-") and instance[3:].isdigit():
        meta["pr_number"] = int(instance[3:])
    return meta


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    instance = sys.argv[2] if len(sys.argv) > 2 else ""

    ev, fatal = _load_evidence(ev_path)

    if fatal is not None:
        # Fail-loud unknown band — terminal. The nonzero exit is the objection; the
        # node's own on_blocked:halt is what stops the pipeline.
        overview = {"summary": {"summary": "", "diagram": None},
                    "overall": {"band": "unknown", "score": 0,
                                "counts": {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}},
                    "cohorts": [], "error": fatal, "meta": _meta(instance, [])}
        _write_overview(overview)
        print(f"Overview risk could not be scored ({fatal}); band=unknown.")
        sys.exit(1)

    cohorts_in = ev.get("cohorts") or []
    file_stats = _file_stats()
    scored = rs.score(cohorts_in, file_stats)
    overall = scored["overall"]
    band = overall["band"]

    files_list = [{"filename": k, "additions": v["additions"], "deletions": v["deletions"]}
                  for k, v in file_stats.items()]
    overview = {
        "files": files_list,
        "summary": {"summary": ev.get("summary") or "", "diagram": ev.get("diagram")},
        "overall": overall,
        "cohorts": _attach_layers(scored["cohorts"], cohorts_in),
        "meta": _meta(instance, file_stats.keys()),
    }
    _write_overview(overview)

    cn = overall["counts"]
    n = len(scored["cohorts"])
    parts = [f"Overview risk: {band} (score {overall['score']:.2f}) — {n} cohort(s): "
             f"{cn['Critical']} Critical, {cn['High']} High, {cn['Medium']} Medium, {cn['Low']} Low."]
    # The agent's risk_band is an advisory hint; the computed band is authoritative.
    hint = ev.get("risk_band")
    if hint and hint != band:
        parts.append(f"(agent hinted risk_band={hint}; computed band {band} is authoritative.)")

    # Per parity decision: only an unknown/garbled run objects. A scored band
    # passes, even Critical — risk is advisory triage.
    print(" ".join(parts))
    sys.exit(0)


def _write_overview(overview):
    out_path = os.environ.get("OVERVIEW_OUT", "/tmp/gh-aw/overview.json")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump(overview, fh)
    except OSError:
        pass


if __name__ == "__main__":
    main()

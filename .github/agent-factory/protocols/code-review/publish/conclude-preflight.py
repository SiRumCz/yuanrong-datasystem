#!/usr/bin/env python3
"""Conclude hook for the preflight gate (4-cluster architecture: adherence / mm-compliance / consistency / security).

Authoritative for blocking. Independently re-reads the 4 cluster branch outputs
from CONCLUDE_INPUTS_DIR (NOT trusting the gate agent's consolidated render in
argv[1], which is used only as display text for the comment):

  adherence.json    - cluster rollup {cluster, legs:[{leg, gather, graded_findings}]}
                      inner legs: spec-solves-issue, plan-implements-spec, code-implements-plan
  mm-compliance.json - single judge evidence {leg?, gather:{verdict,...}, graded_findings}
  consistency.json  - cluster rollup {cluster, legs:[{leg, gather, graded_findings}]}
                      inner legs: docs-updated-appropriately, tests-updated-appropriately
  security.json     - single judge evidence {gather:{verdict:PASS|LOCKED_VIOLATION|n/a,...},
                      graded_findings}

Flatten the two cluster rollups' legs[] into per-leg records, then apply the same
floor-vs-escalation policy at leaf granularity.

Rollup (9 floors unchanged + security floor):
  block if: (issue_linked & !spec_present)
          | (spec_present & spec.verdict=='does-not-solve')
          | (code_changed & !spec_present)
          | (code_changed & !plan_present)
          | plan.verdict=='underspec' | code.verdict=='underplan'
          | mm.verdict=='diverges'
          | docs.verdict=='inadequate'
          | (code & tests.verdict=='inadequate')
          | security.gather.verdict=='LOCKED_VIOLATION'   [NEW security floor]
  warn:    plan.verdict=='overspec' | code.verdict=='overplan'
  n/a contributes nothing.
  missing cluster rollup OR missing inner leg => fail-safe block.
Presence flags are READ from the legs' form-verified scope objects, never recomputed
here (the advance/zone-4 job has neither PR_BODY nor the changed-files list).

ABI: conclude-preflight.py <evidence.json> <instance-key>
  env: BLOCKING ('1'/'0'), CONCLUDE_INPUTS_DIR, PUBLISH_TOKEN, PR, GITHUB_REPOSITORY,
       ENGINE_LOCAL, VERDICT_OUT (optional; default /tmp/gh-aw/verdict.json).
Prints {"conclusion","summary","blocked","reasons":[...],"warnings":[...]}.
"""
import json
import os
import sys

# Import lib from the engine dir (the publish-hook precedent: publish/ -> ../../../engine).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "engine"))
import lib  # noqa: E402

# Cluster definitions: cluster-file -> list of expected inner leg ids (in order).
_ADHERENCE_LEGS = ("spec-solves-issue", "plan-implements-spec", "code-implements-plan")
_CONSISTENCY_LEGS = ("docs-updated-appropriately", "tests-updated-appropriately")

# All 7 per-leg ids in canonical order.
LEGS = ("spec-solves-issue", "plan-implements-spec", "code-implements-plan", "mm-compliance",
        "docs-updated-appropriately", "tests-updated-appropriately", "security")

# Cluster headings for the comment (cluster-file -> display label).
_CLUSTER_HEADINGS = {
    "adherence": "Adherence (spec → plan → code)",
    "mm-compliance": "Mental-model compliance",
    "consistency": "Consistency (docs / tests)",
    "security": "Security",
}
# Which cluster each leg belongs to (for comment grouping).
_LEG_CLUSTER = {
    "spec-solves-issue": "adherence",
    "plan-implements-spec": "adherence",
    "code-implements-plan": "adherence",
    "mm-compliance": "mm-compliance",
    "docs-updated-appropriately": "consistency",
    "tests-updated-appropriately": "consistency",
    "security": "security",
}


def _load_file(name):
    """Read one branch output from CONCLUDE_INPUTS_DIR/<name>.json. Missing/garbled => None."""
    d = os.environ.get("CONCLUDE_INPUTS_DIR", "")
    if not d:
        return None
    try:
        with open(os.path.join(d, f"{name}.json"), encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError):
        return None


def _load_cluster(cluster_name, expected_legs):
    """Load a cluster rollup file and flatten its legs[] into {leg_id: leg_dict}.

    If the cluster file is missing/garbled, or any expected inner leg is absent,
    returns a dict where the missing legs map to None (fail-safe sentinel).
    """
    raw = _load_file(cluster_name)
    result = {}
    if raw is None:
        # Cluster file missing or garbled — all expected legs are absent.
        for leg_id in expected_legs:
            result[leg_id] = None
        return result

    legs_list = raw.get("legs")
    if not isinstance(legs_list, list):
        for leg_id in expected_legs:
            result[leg_id] = None
        return result

    # Index by leg id.
    by_id = {}
    for entry in legs_list:
        if isinstance(entry, dict) and isinstance(entry.get("leg"), str):
            by_id[entry["leg"]] = entry

    for leg_id in expected_legs:
        entry = by_id.get(leg_id)
        # A valid entry must have a 'gather' dict.
        if isinstance(entry, dict) and isinstance(entry.get("gather"), dict):
            result[leg_id] = entry
        else:
            result[leg_id] = None

    return result


def _load_single(file_name, leg_id=None):
    """Load a single judge evidence file (mm-compliance or security).

    Returns (leg_id, leg_dict) where leg_dict is None if missing/garbled.
    The returned dict is normalised to the same per-leg shape:
      {gather: {...}, graded_findings: [...]}
    For security the gather IS the top-level evidence (no nesting needed — the
    security evidence schema puts gather-style fields at the root).
    """
    raw = _load_file(file_name)
    key = leg_id or file_name
    if raw is None:
        return key, None
    # mm-compliance and security single-judge evidences may present as:
    #   A) {gather: {...}, graded_findings: [...]}  (standard judge shape)
    #   B) {verdict: ..., graded_findings: [...]}   (flat — treat root as gather)
    if isinstance(raw.get("gather"), dict):
        return key, raw  # already in the right shape
    # Treat the whole object as the gather (security schema puts fields at root).
    return key, {"gather": raw, "graded_findings": raw.get("graded_findings", [])}


def _gather(leg):
    """Return the gather dict from a per-leg record, or {} if absent/garbled."""
    if leg is None:
        return {}
    g = leg.get("gather")
    return g if isinstance(g, dict) else {}


def _verdict(leg):
    v = _gather(leg).get("verdict")
    return v if isinstance(v, str) else "n/a"


def _scope(leg):
    s = _gather(leg).get("scope")
    return s if isinstance(s, dict) else {}


def _flag(leg, key):
    return bool(_scope(leg).get(key, False))


def _has_blocking_grade(leg):
    if leg is None:
        return False
    return any(isinstance(g, dict) and g.get("severity") == "blocking"
               for g in (leg.get("graded_findings") or []))


def _present(leg):
    """A leg whose evidence is missing/garbled (None or no gather dict) => fail-safe block."""
    return leg is not None and isinstance(leg.get("gather"), dict)


def rollup(spec_leg, plan_leg, code_leg, mm_leg, docs_leg, tests_leg, security_leg):
    """Return (reasons[], warnings[]) for the 4-cluster preflight.

    All nine original floor conditions are preserved unchanged, sourced through the
    cluster-flattened per-leg records. The security floor (LOCKED_VIOLATION) is new.
    Missing/garbled leg (None) => fail-safe block.
    """
    reasons, warnings = [], []

    issue_linked = _flag(spec_leg, "issue_linked")
    spec_present = _flag(spec_leg, "spec_present") or _flag(plan_leg, "spec_present")
    plan_present = _flag(plan_leg, "plan_present") or _flag(code_leg, "plan_present")
    code_changed = _flag(plan_leg, "code_changed") or _flag(code_leg, "code_changed")

    spec_v, plan_v, code_v = _verdict(spec_leg), _verdict(plan_leg), _verdict(code_leg)

    if issue_linked and not spec_present:
        reasons.append("issue is linked but no spec is present")
    if spec_present and spec_v == "does-not-solve":
        reasons.append("the spec does not solve the linked issue")
    if code_changed and not spec_present:
        reasons.append("code changed but no spec is present")
    if code_changed and not plan_present:
        reasons.append("code changed but no plan is present")
    if plan_v == "underspec":
        reasons.append("plan does not implement the spec (underspec)")
    if code_v == "underplan":
        reasons.append("code does not implement the plan (underplan)")
    if _verdict(mm_leg) == "diverges":
        reasons.append("the PR diverges from the stored mental model")
    if _verdict(docs_leg) == "inadequate":
        reasons.append("relevant docs are not updated appropriately")
    if code_changed and _verdict(tests_leg) == "inadequate":
        reasons.append("relevant tests are not updated appropriately")

    # Security floor (NEW): LOCKED_VIOLATION is a deterministic block the judge cannot remove.
    if _present(security_leg) and _gather(security_leg).get("verdict") == "LOCKED_VIOLATION":
        reasons.append("security: LOCKED_VIOLATION detected — deterministic security floor (cannot be overridden by judge grades)")

    if plan_v == "overspec":
        warnings.append("plan adds items beyond the spec (overspec)")
    if code_v == "overplan":
        warnings.append("code adds changes beyond the plan (overplan)")

    # fail-safe: a missing/garbled leg (None) blocks.
    all_legs = [
        ("spec-solves-issue", spec_leg),
        ("plan-implements-spec", plan_leg),
        ("code-implements-plan", code_leg),
        ("mm-compliance", mm_leg),
        ("docs-updated-appropriately", docs_leg),
        ("tests-updated-appropriately", tests_leg),
        ("security", security_leg),
    ]
    for name, leg in all_legs:
        if not _present(leg):
            reasons.append(f"{name}: evidence missing or unreadable (fail-safe block)")

    # escalation: an in-scope, non-floor leg the judge graded blocking.
    FLOOR_VERDICTS = {"does-not-solve", "underspec", "underplan", "diverges", "inadequate",
                      "LOCKED_VIOLATION"}
    for name, leg in all_legs:
        if _present(leg) and _verdict(leg) not in FLOOR_VERDICTS and _has_blocking_grade(leg):
            reasons.append(f"{name}: judge flagged a blocking finding")

    return reasons, warnings


def _render_comment(status, reasons, warnings, spec_leg, plan_leg, code_leg, mm_leg, docs_leg, tests_leg, security_leg):
    """Build the single consolidated comment body grouped under 4 cluster headings.

    Agent-supplied summaries are concatenated into this string; the whole body is
    passed to lib.post_pr_comment as ONE `gh api -f body=BODY` argument (an argument
    vector, never shell-interpolated).
    """
    icon = "\U0001f6d1" if status == "blocked" else "✅"
    lines = [f"{icon} **Preflight {status}** — adherence · mental-model · consistency · security", ""]

    def _leg_row(name, leg):
        v = _verdict(leg)
        extra = " · judge:blocking" if _has_blocking_grade(leg) else ""
        return f"| {name} | `{v}`{extra} |"

    # Adherence cluster
    lines.append(f"**{_CLUSTER_HEADINGS['adherence']}**")
    lines.append("| leg | verdict |")
    lines.append("|---|---|")
    for name, leg in (("spec-solves-issue", spec_leg),
                      ("plan-implements-spec", plan_leg),
                      ("code-implements-plan", code_leg)):
        lines.append(_leg_row(name, leg))
    lines.append("")

    # mm-compliance cluster
    lines.append(f"**{_CLUSTER_HEADINGS['mm-compliance']}**")
    lines.append("| leg | verdict |")
    lines.append("|---|---|")
    lines.append(_leg_row("mm-compliance", mm_leg))
    lines.append("")

    # Consistency cluster
    lines.append(f"**{_CLUSTER_HEADINGS['consistency']}**")
    lines.append("| leg | verdict |")
    lines.append("|---|---|")
    for name, leg in (("docs-updated-appropriately", docs_leg),
                      ("tests-updated-appropriately", tests_leg)):
        lines.append(_leg_row(name, leg))
    lines.append("")

    # Security cluster
    lines.append(f"**{_CLUSTER_HEADINGS['security']}**")
    lines.append("| leg | verdict |")
    lines.append("|---|---|")
    lines.append(_leg_row("security", security_leg))

    if reasons:
        lines += ["", "**Blocking:**"] + [f"- {r}" for r in reasons]
    if warnings:
        lines += ["", "**Advisory:**"] + [f"- {w}" for w in warnings]
    if status == "blocked":
        lines += ["", "_Halted — a maintainer `/override` advances past the gate._"]
    return "\n".join(lines)


def _load_legs():
    """Load the 4 cluster branch outputs and flatten to 7 per-leg dicts (None = missing).

    Returns a dict: leg_id -> leg_dict | None
    """
    legs = {}

    # adherence cluster rollup.
    adherence_legs = _load_cluster("adherence", _ADHERENCE_LEGS)
    legs.update(adherence_legs)

    # mm-compliance single judge evidence.
    _, mm_leg = _load_single("mm-compliance", "mm-compliance")
    legs["mm-compliance"] = mm_leg

    # consistency cluster rollup.
    consistency_legs = _load_cluster("consistency", _CONSISTENCY_LEGS)
    legs.update(consistency_legs)

    # security single judge evidence.
    _, security_leg = _load_single("security", "security")
    legs["security"] = security_leg

    return legs


def main():
    blocking = os.environ.get("BLOCKING", "") == "1"

    legs = _load_legs()
    spec_leg = legs.get("spec-solves-issue")
    plan_leg = legs.get("plan-implements-spec")
    code_leg = legs.get("code-implements-plan")
    mm_leg = legs.get("mm-compliance")
    docs_leg = legs.get("docs-updated-appropriately")
    tests_leg = legs.get("tests-updated-appropriately")
    security_leg = legs.get("security")

    reasons, warnings = rollup(spec_leg, plan_leg, code_leg, mm_leg, docs_leg, tests_leg, security_leg)
    blocked = bool(blocking or reasons)
    if blocking:
        reasons = reasons + ["engine blocking signal"]
    status = "blocked" if blocked else "clear"

    # verdict.json — custody-shaped payload (folds in the retired publish-verdict role).
    records = []
    for name, leg in (("spec-solves-issue", spec_leg),
                      ("plan-implements-spec", plan_leg),
                      ("code-implements-plan", code_leg),
                      ("mm-compliance", mm_leg),
                      ("docs-updated-appropriately", docs_leg),
                      ("tests-updated-appropriately", tests_leg),
                      ("security", security_leg)):
        records.append({"type": "leg", "leg": name,
                        "verdict": _verdict(leg), "scope": _scope(leg)})
    records.append({"type": "verdict", "status": status, "blocked": blocked,
                    "blocking": bool(blocking), "reasons": reasons, "warnings": warnings})
    payload = {"records": records}
    inst = sys.argv[2] if len(sys.argv) > 2 else ""
    if inst.startswith("pr-") and inst[3:].isdigit():
        payload["meta"] = {"pr_number": int(inst[3:]),
                           "head_sha": os.environ.get("HEAD_SHA", "")}
    out_path = os.environ.get("VERDICT_OUT", "/tmp/gh-aw/verdict.json")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError:
        pass

    pr = os.environ.get("PR", "")
    if pr:
        body = _render_comment(status, reasons, warnings,
                               spec_leg, plan_leg, code_leg, mm_leg, docs_leg, tests_leg, security_leg)
        lib.post_pr_comment(pr, body)

    summary = ("Preflight blocked: " + "; ".join(reasons)) if blocked else "Preflight clear."
    print(json.dumps({"conclusion": "blocked" if blocked else "clear",
                      "summary": summary, "blocked": blocked,
                      "reasons": reasons, "warnings": warnings}))


if __name__ == "__main__":
    main()

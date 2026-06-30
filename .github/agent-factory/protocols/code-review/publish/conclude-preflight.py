#!/usr/bin/env python3
"""Conclude hook for the preflight gate (4-cluster architecture: adherence / mm-compliance / consistency / security).

Authoritative for blocking. The load-bearing facts of each leg — its `scope`
flags and its gather `verdict` — are read directly from that leg's PERSISTED
GATHER EVIDENCE in the state checkout (CONCLUDE_STATE_DIR), NOT from any LLM's
echo. The gather evidence is the deterministic source the engine already holds;
an LLM judge/rollup copying those facts forward proved unreliable (it could not
reproduce the gather_verdict/scope a deterministic check recomputes anyway), so
the copy is removed from the load-bearing path entirely.

The 4 cluster branch outputs (CONCLUDE_INPUTS_DIR) are still read, but ONLY for
their per-leg `graded_findings` — the judges' severity grades, used for
escalation (additive: a grade can only block harder than the floor, never clear
it). The gate agent's consolidated render in argv[1] is display text only.

  CONCLUDE_STATE_DIR/<pid>/<instance>/<dotted-gather-path>.evidence.json
                    - per-leg {scope, verdict, ...}  (LOAD-BEARING: floors + presence)
  CONCLUDE_INPUTS_DIR/adherence.json / consistency.json
                    - cluster rollup {cluster, legs:[{leg, graded_findings}]}
  CONCLUDE_INPUTS_DIR/mm-compliance.json / security.json
                    - single judge evidence {leg, graded_findings, ...}

Rollup (9 floors unchanged + security floor):
  block if: (issue_linked & !spec_present)
          | (spec_present & spec.verdict=='does-not-solve')
          | (code_changed & !spec_present)
          | (code_changed & !plan_present)
          | plan.verdict=='underspec' | code.verdict=='underplan'
          | mm.verdict=='diverges'
          | docs.verdict=='inadequate'
          | (code & tests.verdict=='inadequate')
          | security.verdict=='LOCKED_VIOLATION'
  warn:    plan.verdict=='overspec' | code.verdict=='overplan'
  n/a contributes nothing.
  missing/unreadable GATHER evidence for a leg => fail-safe block.

ABI: conclude-preflight.py <evidence.json> <instance-key>
  env: BLOCKING ('1'/'0'), CONCLUDE_STATE_DIR, CONCLUDE_INPUTS_DIR, PUBLISH_TOKEN,
       PR, GITHUB_REPOSITORY, ENGINE_LOCAL, VERDICT_OUT (default /tmp/gh-aw/verdict.json).
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

# protocol.json sits one level up from publish/.
_PROTO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "protocol.json")

# leg -> the gather node's TREE path. Its persisted evidence lives at
# <state>/<pid>/<instance>/<dot-joined-file-path>.evidence.json (state_path drops
# nothing for this multi-phase protocol, so the dotted name is the full tree path).
_GATHER_TREE_PATH = {
    "spec-solves-issue": ["preflight", "adherence", "adherence-fanout",
                          "spec-solves-issue", "spec-solves-issue-gather"],
    "plan-implements-spec": ["preflight", "adherence", "adherence-fanout",
                             "plan-implements-spec", "plan-implements-spec-gather"],
    "code-implements-plan": ["preflight", "adherence", "adherence-fanout",
                             "code-implements-plan", "code-implements-plan-gather"],
    "docs-updated-appropriately": ["preflight", "consistency", "consistency-fanout",
                                   "docs-updated-appropriately", "docs-updated-appropriately-gather"],
    "tests-updated-appropriately": ["preflight", "consistency", "consistency-fanout",
                                    "tests-updated-appropriately", "tests-updated-appropriately-gather"],
    "mm-compliance": ["preflight", "mm-compliance", "mm-compliance-gather"],
    "security": ["preflight", "security", "security-gather"],
}


def _load_proto():
    try:
        with open(_PROTO_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def gather_evidence_path(state_dir, pid, instance, leg):
    """Deterministic path to a leg's persisted gather evidence.json in the state
    checkout, or None if the protocol/leg is unknown. (Also used by tests to
    place fixtures at exactly the path this hook reads — one source of truth.)"""
    proto = _load_proto()
    tree_path = _GATHER_TREE_PATH.get(leg)
    if proto is None or tree_path is None or not state_dir:
        return None
    return lib.output_artifact_path(state_dir, pid, instance,
                                    path=lib.state_path(proto, tree_path), kind="evidence")


def _load_gather_facts(state_dir, pid, instance):
    """Read {scope, verdict} per leg from each leg's persisted gather evidence.
    Returns {leg: {"scope": dict, "verdict": str} | None}. A leg whose gather
    evidence is missing/garbled/verdict-less maps to None (=> fail-safe block)."""
    facts = {}
    for leg in LEGS:
        facts[leg] = None
        path = gather_evidence_path(state_dir, pid, instance, leg)
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                ev = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(ev, dict) or not isinstance(ev.get("verdict"), str):
            continue
        facts[leg] = {"scope": ev["scope"] if isinstance(ev.get("scope"), dict) else {},
                      "verdict": ev["verdict"]}
    return facts

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
        # Read only for graded_findings now; accept any dict cell that carries a
        # recognizable field (scope/gather_verdict/graded_findings — grades-only OK).
        if isinstance(entry, dict) and (isinstance(entry.get("scope"), dict) or
                                         isinstance(entry.get("gather_verdict"), str) or
                                         isinstance(entry.get("graded_findings"), list)):
            result[leg_id] = entry
        else:
            result[leg_id] = None

    return result


def _load_single(file_name, leg_id=None):
    """Load a single judge evidence file (mm-compliance or security).

    Returns (leg_id, leg_dict) where leg_dict is None if missing/garbled.
    The returned dict is in the lightened per-leg shape:
      {scope: {...}, gather_verdict: str, graded_findings: [...]}
    """
    raw = _load_file(file_name)
    key = leg_id or file_name
    if raw is None:
        return key, None
    # Read only for graded_findings now; accept any recognizable judge-evidence shape.
    if (isinstance(raw.get("gather_verdict"), str) or isinstance(raw.get("scope"), dict)
            or isinstance(raw.get("graded_findings"), list)):
        return key, raw
    # Legacy fallback: {gather: {...}, graded_findings: [...]} — extract scope+verdict.
    g = raw.get("gather")
    if isinstance(g, dict):
        return key, {"scope": g.get("scope") or {}, "gather_verdict": g.get("verdict", "n/a"),
                     "graded_findings": raw.get("graded_findings", [])}
    return key, None


def _verdict(leg):
    v = leg.get("gather_verdict") if isinstance(leg, dict) else None
    return v if isinstance(v, str) else "n/a"


def _scope(leg):
    s = leg.get("scope") if isinstance(leg, dict) else None
    return s if isinstance(s, dict) else {}


def _flag(leg, key):
    return bool(_scope(leg).get(key, False))


def _has_blocking_grade(leg):
    if leg is None:
        return False
    return any(isinstance(g, dict) and g.get("severity") == "blocking"
               for g in (leg.get("graded_findings") or []))


def _present(leg):
    """A leg whose evidence is missing/garbled (None or no scope/gather_verdict) => fail-safe block."""
    return leg is not None and (isinstance(leg.get("scope"), dict) or
                                 isinstance(leg.get("gather_verdict"), str))


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
    if _present(security_leg) and _verdict(security_leg) == "LOCKED_VIOLATION":
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
    instance = sys.argv[2] if len(sys.argv) > 2 else ""
    pid = (_load_proto() or {}).get("name", "code-review")
    state_dir = os.environ.get("CONCLUDE_STATE_DIR", "")

    # Load-bearing facts (scope + verdict) from each leg's persisted gather evidence.
    gather_facts = _load_gather_facts(state_dir, pid, instance)
    # Cluster/judge branch outputs — read ONLY for per-leg graded_findings (escalation).
    rollup_legs = _load_legs()

    legs = {}
    for leg in LEGS:
        gf = gather_facts.get(leg)
        if gf is None:
            legs[leg] = None  # fail-safe: no trustworthy gather facts for this leg
            continue
        grades = (rollup_legs.get(leg) or {}).get("graded_findings")
        legs[leg] = {"scope": gf["scope"], "gather_verdict": gf["verdict"],
                     "graded_findings": grades if isinstance(grades, list) else []}

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
    if instance.startswith("pr-") and instance[3:].isdigit():
        payload["meta"] = {"pr_number": int(instance[3:]),
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

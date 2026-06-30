#!/usr/bin/env python3
"""Conclude hook for the preflight gate (Phase C: the 3-leg issue->spec->plan->code chain + mm-compliance + docs/tests).

Authoritative for blocking. Independently re-reads the chain legs from
CONCLUDE_INPUTS_DIR (NOT trusting the gate agent's consolidated render in argv[1],
which is used only as display text for the comment), reads each leg's
form-verified `verdict` + `scope` flags, and applies the block-gaps / warn-extras
policy. Posts ONE consolidated preflight comment, writes verdict.json, and prints
{conclusion,summary,blocked,reasons,warnings}. blocked=True + the gate node's
`on_blocked: halt` halts the run until a maintainer /overrides.

Rollup (chain + mm-compliance + docs/tests):
  block if: (issue_linked & !spec_present)
          | (spec_present & spec.verdict=='does-not-solve')
          | (code_changed & !spec_present)
          | (code_changed & !plan_present)
          | plan.verdict=='underspec' | code.verdict=='underplan'
          | mm.verdict=='diverges'
          | docs.verdict=='inadequate'
          | (code & tests.verdict=='inadequate')
  warn:    plan.verdict=='overspec' | code.verdict=='overplan'
  n/a contributes nothing.
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

LEGS = ("spec-solves-issue", "plan-implements-spec", "code-implements-plan", "mm-compliance",
        "docs-updated-appropriately", "tests-updated-appropriately")


def _load_leg(name):
    """Read one leg evidence from CONCLUDE_INPUTS_DIR/<name>.json. Missing/garbled =>
    {} (a leg the rollup treats as no-signal; the join guarantees real legs reach done
    before conclude runs, so {} only happens off the live path, e.g. a unit smoke)."""
    d = os.environ.get("CONCLUDE_INPUTS_DIR", "")
    if not d:
        return {}
    try:
        with open(os.path.join(d, f"{name}.json"), encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _verdict(leg):
    v = leg.get("verdict")
    return v if isinstance(v, str) else "n/a"


def _scope(leg):
    s = leg.get("scope")
    return s if isinstance(s, dict) else {}


def _flag(leg, key):
    return bool(_scope(leg).get(key, False))


def rollup(spec_leg, plan_leg, code_leg, mm_leg, docs_leg, tests_leg):
    """Return (reasons[], warnings[]) for the Phase-C preflight (chain + mm-compliance + docs/tests). Reasons => block."""
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

    if plan_v == "overspec":
        warnings.append("plan adds items beyond the spec (overspec)")
    if code_v == "overplan":
        warnings.append("code adds changes beyond the plan (overplan)")

    return reasons, warnings


def _render_comment(status, reasons, warnings, spec_leg, plan_leg, code_leg, mm_leg, docs_leg, tests_leg):
    """Build the single consolidated comment body. Agent-supplied summaries are
    concatenated into this string; the whole body is passed to lib.post_pr_comment
    as ONE `gh api -f body=BODY` argument (an argument vector, never shell-interpolated)."""
    icon = "\U0001f6d1" if status == "blocked" else "✅"
    lines = [f"{icon} **Preflight {status}** — issue → spec → plan → code + mental-model + docs/tests", ""]
    rows = [("spec-solves-issue", spec_leg), ("plan-implements-spec", plan_leg),
            ("code-implements-plan", code_leg), ("mm-compliance", mm_leg),
            ("docs-updated-appropriately", docs_leg), ("tests-updated-appropriately", tests_leg)]
    lines.append("| leg | verdict |")
    lines.append("|---|---|")
    for name, leg in rows:
        lines.append(f"| {name} | `{_verdict(leg)}` |")
    if reasons:
        lines += ["", "**Blocking:**"] + [f"- {r}" for r in reasons]
    if warnings:
        lines += ["", "**Advisory:**"] + [f"- {w}" for w in warnings]
    if status == "blocked":
        lines += ["", "_Halted — a maintainer `/override` advances past the gate._"]
    return "\n".join(lines)


def main():
    blocking = os.environ.get("BLOCKING", "") == "1"
    spec_leg = _load_leg("spec-solves-issue")
    plan_leg = _load_leg("plan-implements-spec")
    code_leg = _load_leg("code-implements-plan")
    mm_leg = _load_leg("mm-compliance")
    docs_leg = _load_leg("docs-updated-appropriately")
    tests_leg = _load_leg("tests-updated-appropriately")

    reasons, warnings = rollup(spec_leg, plan_leg, code_leg, mm_leg, docs_leg, tests_leg)
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
                      ("tests-updated-appropriately", tests_leg)):
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
        body = _render_comment(status, reasons, warnings, spec_leg, plan_leg, code_leg, mm_leg, docs_leg, tests_leg)
        lib.post_pr_comment(pr, body)

    summary = ("Preflight blocked: " + "; ".join(reasons)) if blocked else "Preflight clear."
    print(json.dumps({"conclusion": "blocked" if blocked else "clear",
                      "summary": summary, "blocked": blocked,
                      "reasons": reasons, "warnings": warnings}))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Shared form-check logic for the docs/tests coherence legs (docs-coverage, tests-coverage).

A coherence leg's agent self-identifies the docs (resp. tests) relevant to the change and
judges each. This verifies the EVIDENCE FORM, never the substance:
  - scope.code_changed matches an independent recompute from changed-files;
  - applicability: a leg that is N/A-when-no-code (tests) passes on verdict 'n/a' + empty
    items + verified code_changed False; an always-applicable leg (docs) is never N/A;
  - examined is a non-empty trace; items is a list of {path, status in the legal set};
  - every path is domain-shaped (is_doc / is_test);
  - every 'updated_appropriately'/'inadequate' item's path was actually changed in the PR
    (appears in changed-files) — the agent cannot claim a doc/test it never touched;
  - verdict is consistent: 'inadequate' iff any item is 'missing' or 'inadequate'.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402

LEGAL_STATUS = {"updated_appropriately", "missing", "inadequate"}


def evaluate(name, evidence, changed_files, *, is_kind, kind_label, applicable_without_code):
    """Return {check, pass, feedback}. is_kind: _paths.is_doc | _paths.is_test;
    kind_label: 'doc' | 'test'; applicable_without_code: True for docs, False for tests."""
    def out(ok, fb):
        return {"check": name, "pass": ok, "feedback": fb}

    if not isinstance(evidence, dict):
        return out(False, "evidence is not a JSON object")
    code_changed = any(_paths.is_code(p) for p in changed_files)
    scope = evidence.get("scope") or {}
    if bool(scope.get("code_changed")) != code_changed:
        return out(False, f"scope disagreement: agent code_changed={bool(scope.get('code_changed'))} "
                          f"recompute={code_changed}")

    verdict = evidence.get("verdict")
    items = evidence.get("items")
    examined = evidence.get("examined")

    if not applicable_without_code and not code_changed:
        if verdict == "n/a" and items == []:
            return out(True, "verified N/A (no code change; empty items).")
        return out(False, "no code change but verdict is not n/a with empty items")

    if not isinstance(examined, list) or not examined:
        return out(False, "examined must be a non-empty list")
    if not isinstance(items, list):
        return out(False, "items must be a list")

    changed = set(changed_files)
    bad = []
    has_problem = False
    for it in items:
        if not isinstance(it, dict) or not it.get("path") or it.get("status") not in LEGAL_STATUS:
            bad.append("malformed item (need path + status in updated_appropriately|missing|inadequate)")
            continue
        path, status = it["path"], it["status"]
        if not is_kind(path):
            bad.append(f"path is not a {kind_label} path: {path!r}")
        if status in ("updated_appropriately", "inadequate") and path not in changed:
            bad.append(f"{status} {kind_label} not in the diff (was not changed): {path!r}")
        if status in ("missing", "inadequate"):
            has_problem = True

    expected = "inadequate" if has_problem else "adequate"
    if verdict != expected:
        bad.append(f"verdict {verdict!r} inconsistent with items (expected {expected!r})")

    if bad:
        return out(False, "; ".join(bad[:6]))
    return out(True, f"{kind_label} coherence form valid ({expected}).")


def finding_refs(evidence):
    """The list of item paths the judge must grade (one severity per item)."""
    items = evidence.get("items") if isinstance(evidence, dict) else None
    return [it["path"] for it in (items or []) if isinstance(it, dict) and it.get("path")]

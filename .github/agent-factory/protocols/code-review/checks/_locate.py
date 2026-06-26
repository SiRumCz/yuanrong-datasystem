"""Spec/plan artifact location — ports custody locate.js (detectSpecInBody,
detectPlanInBody, locateArtifact's association layer).

Pure: callers supply the PR body + changed paths; file/probe I/O stays in the
caller (mirrors custody's `io` injection). Shared by spec-present, plan-present,
adherence-coverage, and the preflight-agent prefetch so the "is this artifact
ASSOCIATED with the PR?" rule has ONE source of truth.

custody parity: an artifact counts as associated only when the PR brings it in
its own diff (a changed spec/plan path) OR writes it into the body (a
requirements/plan heading with a non-empty section, or — plan only — a task
checklist). Spec additionally falls back to the whole PR description as the
claim. A repo doc that merely exists is never attributed (that blocked unrelated
CI/registration PRs in custody)."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402  (is_spec_path / is_plan_path — changed-path classification)

ARTIFACT_MAX_CHARS = 12000

# Body detectors — mirror custody locate.js detectSpecInBody / detectPlanInBody.
_SPEC_HEADING = re.compile(r"^#{1,6}\s*(requirements?|spec(?:ification)?)\b.*$", re.I | re.M)
_PLAN_HEADING = re.compile(r"^#{1,6}\s*(implementation\s+plan|plan)\b.*$", re.I | re.M)
_CHECKLIST = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+.+$", re.M)
_HEADING_SPLIT = re.compile(r"^#{1,6}\s", re.M)
_NON_WS = re.compile(r"\S")


def detect_spec_in_body(body):
    """A requirements/spec heading whose section has content → the heading text."""
    if not body:
        return None
    m = _SPEC_HEADING.search(body)
    if not m:
        return None
    after = body[body.index(m.group(0)) + len(m.group(0)):]
    section = _HEADING_SPLIT.split(after)[0] if after else ""
    if not _NON_WS.search(section):
        return None
    return m.group(0).strip()


def detect_plan_in_body(body):
    """An implementation-plan heading, else a markdown task checklist."""
    if not body:
        return None
    m = _PLAN_HEADING.search(body)
    if m:
        return m.group(0).strip()
    if _CHECKLIST.search(body):
        return "task checklist in PR description"
    return None


def _is_path(kind):
    return _paths.is_spec_path if kind == "spec" else _paths.is_plan_path


def locate(kind, body, changed_paths):
    """Resolve whether a spec/plan artifact is associated with this PR.

    Returns {found, source, body_hit, changed_hits, evidence}; source is one of
    'file', 'body-section', 'pr-description', or None. Order mirrors custody:
    diff/body association first, then (spec only) the description-as-claim
    fallback."""
    is_path = _is_path(kind)
    changed_hits = [p for p in (changed_paths or []) if is_path(p)]
    body_hit = detect_spec_in_body(body) if kind == "spec" else detect_plan_in_body(body)

    evidence = []
    if body_hit:
        evidence.append({"label": "PR body", "detail": body_hit})
    for p in changed_hits:
        evidence.append({"label": "spec file" if kind == "spec" else "plan file", "detail": p})
    if evidence:
        return {"found": True, "source": "file" if changed_hits else "body-section",
                "body_hit": body_hit, "changed_hits": changed_hits, "evidence": evidence}

    # Layer 2 (spec only): no committed spec file and no structured requirements
    # section, but the PR has a description → treat the description as the claim.
    # Plan has no such fallback — a description is a claim, not an implementation plan.
    if kind == "spec" and body and _NON_WS.search(body):
        return {"found": True, "source": "pr-description", "body_hit": None, "changed_hits": [],
                "evidence": [{"label": "PR description",
                              "detail": "No committed spec file or requirements section — "
                                        "using the PR description as the requirements/claim."}]}
    return {"found": False, "source": None, "body_hit": None, "changed_hits": [], "evidence": []}


def artifact_text(kind, body, changed_hits, read_file):
    """Resolve the artifact TEXT to judge adherence against (capped). Prefer the
    committed file (read via the injected `read_file(path)->str|None`); else the
    body section / description slice. Mirrors custody locateArtifact's text arm."""
    if changed_hits:
        raw = read_file(changed_hits[0])
        if raw:
            return raw[:ARTIFACT_MAX_CHARS]
    if body:
        if kind == "spec":
            hit = detect_spec_in_body(body)
            idx = body.index(hit) if hit and hit in body else 0
            return body[idx:idx + ARTIFACT_MAX_CHARS]
        hit = detect_plan_in_body(body)
        if hit and hit in body:
            idx = body.index(hit)
            return body[idx:idx + ARTIFACT_MAX_CHARS]
    return ""

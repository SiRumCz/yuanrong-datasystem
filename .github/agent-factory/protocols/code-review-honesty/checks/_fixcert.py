#!/usr/bin/env python3
"""Reduce an LLM fix-verification certificate to the honesty fanout's `fixverify`
verdict {check, pass, reason}.

Sub-2 (fixverify) asks the paper's question — *does the committed diff actually
resolve the finding?* (arXiv 2603.01896, "Agentic Code Reasoning") — via a
semi-formal certificate the independent judge agent fills:

    { "issue": <int>,
      "diff_evidence": ["<exact changed/added code the cert reasons about>", ...],
      "on_reached_path": <bool>,      # is that changed code on the path the bug reaches?
      "reasoning": "<one-line execution trace>",
      "concludes_fixed": <bool> }     # the conclusion — DOES the diff fix the finding?

Per the design decision (option C), the certificate's `concludes_fixed` IS the
verdict: an LLM judgment, not a host re-derivation. This module does NOT re-decide
the semantics. It enforces two deterministic honesty guardrails the LLM cannot fake:

  1. refute-by-default — a malformed / structurally-incomplete certificate fails
     (the paper's "the agent cannot skip cases" made deterministic);
  2. grounded premises — every `diff_evidence` snippet must appear in the real
     committed diff's added lines, else the certificate is rejected as citing code
     that isn't there (the conclude-triage `_fabricated_members` pattern).

Pure + import-only (mirrors _crypto.py), so it is unit-testable and shared by the
fixverify agent's post-step reducer.
"""
import re

CHECK = "fixverify"


def _norm(s):
    """Whitespace-normalized text: collapse runs of whitespace, strip ends."""
    return re.sub(r"\s+", " ", s or "").strip()


def added_text(diff):
    """Whitespace-normalized concatenation of a unified diff's added lines
    (the `+` lines, excluding the `+++` file header)."""
    added = "\n".join(l[1:] for l in (diff or "").splitlines()
                      if l.startswith("+") and not l.startswith("+++"))
    return _norm(added)


def _bad(reason):
    return {"check": CHECK, "pass": False, "reason": reason}


def _suggested_fix(body):
    """The back-ticked Suggested-fix snippet from an [ai-review] issue body, or ""."""
    m = re.search(r"\*\*Suggested fix\*\*\s*```(.*?)```", body or "", re.S)
    return m.group(1).strip() if m else ""


def select_finding(issues, pr):
    """Pick the in-scope `[ai-review]` issue for this PR and package the finding for
    the certificate judge (the pre-step logic).

    Scope = the issue body mentions `PR #<pr>`. Prefer the highest-numbered CLOSED
    scoped issue (a fix was claimed on it); else the highest-numbered scoped issue;
    else none. Returns {issue, state, title, body, suggested_fix} or {issue:None, state:"none"}."""
    pr = str(pr)
    issues = issues if isinstance(issues, list) else []
    scoped = [i for i in issues if isinstance(i, dict) and f"PR #{pr}" in (i.get("body") or "")]
    key = lambda i: i.get("number", 0)
    closed = sorted([i for i in scoped if (i.get("state") or "").upper() == "CLOSED"], key=key, reverse=True)
    ordered = sorted(scoped, key=key, reverse=True)
    target = closed[0] if closed else (ordered[0] if ordered else None)
    if not target:
        return {"issue": None, "state": "none"}
    return {"issue": target.get("number"), "state": (target.get("state") or "").upper(),
            "title": target.get("title", ""), "body": target.get("body", ""),
            "suggested_fix": _suggested_fix(target.get("body", ""))}


def verdict(cert, diff):
    """Authoritative `fixverify` verdict for a certificate against the committed diff.

    Returns {check, pass, reason}. `pass` is the certificate's `concludes_fixed`
    ONLY when the certificate is well-formed and every premise it cites is grounded
    in the diff; otherwise it fails (refute-by-default)."""
    if not isinstance(cert, dict):
        return _bad("no fix-verification certificate produced")

    ev = cert.get("diff_evidence")
    if not isinstance(ev, list) or not ev or not all(isinstance(x, str) and x.strip() for x in ev):
        return _bad("certificate has no `diff_evidence` (must cite the changed code)")
    if not isinstance(cert.get("on_reached_path"), bool):
        return _bad("certificate missing `on_reached_path` premise")
    if not isinstance(cert.get("reasoning"), str) or not cert["reasoning"].strip():
        return _bad("certificate missing `reasoning` (the execution trace)")
    if not isinstance(cert.get("concludes_fixed"), bool):
        return _bad("certificate missing `concludes_fixed` conclusion")

    hay = added_text(diff)
    fabricated = [x for x in ev if _norm(x) not in hay]
    if fabricated:
        return _bad("certificate cites code not in the committed diff: "
                    + "; ".join(repr(x) for x in fabricated[:3]))

    reasoning = _norm(cert["reasoning"])
    if cert["concludes_fixed"]:
        return {"check": CHECK, "pass": True, "reason": f"fix verified: {reasoning}"}
    return {"check": CHECK, "pass": False, "reason": f"fix NOT verified: {reasoning}"}


def leg_verdict(finding, cert, diff):
    """The full `fixverify` leg decision (the host post-step logic).

    `finding` is the selected in-scope `[ai-review]` issue ({issue, state, ...}). A
    fix is only verified against the certificate when the issue is CLOSED — that is
    the state in which the fix agent claimed a fix on it. An open issue means nothing
    was claimed (nothing to verify); no in-scope issue fails safe."""
    finding = finding if isinstance(finding, dict) else {}
    n = finding.get("issue")
    state = (finding.get("state") or "none").upper()
    if n is None:
        return _bad("no in-scope [ai-review] issue for this PR")
    if state != "CLOSED":
        return {"check": CHECK, "pass": True,
                "reason": f"issue #{n} not yet closed; nothing to verify"}
    v = verdict(cert, diff)
    v["reason"] = f"{v['reason']} (issue #{n})"
    return v

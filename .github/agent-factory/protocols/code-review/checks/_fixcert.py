#!/usr/bin/env python3
"""Reduce an LLM fix-verification certificate to the honesty fanout's `fixverify`
verdict {check, pass, reason}.

Sub-2 (fixverify) asks the paper's question — *does the committed diff actually
resolve the finding?* (arXiv 2603.01896, "Agentic Code Reasoning") — via a
semi-formal certificate the independent judge agent fills:

    { "issue": <int>,
      "premises": ["<grounded claims about the patch>", ...],
      "execution_trace": "<one-line execution trace of the patched code>",
      "verdict": "resolved" | "not_resolved",
      "counterexample": "<example showing the finding is not fixed>" | absent,
      "no_counterexample_proof": "<reasoning why no counterexample exists>" | absent,
      "concludes_fixed": <bool> }     # must agree with verdict: resolved iff concludes_fixed

Per the design decision (option C), the certificate's `concludes_fixed` IS the
verdict: an LLM judgment, not a host re-derivation. This module does NOT re-decide
the semantics. It enforces three deterministic honesty guardrails the LLM cannot fake:

  1. refute-by-default — a malformed / structurally-incomplete certificate fails
     (the paper's "the agent cannot skip cases" made deterministic);
  2. grounded premises — every premise snippet must appear in the real committed
     diff's added lines, else the certificate is rejected as citing code that
     isn't there (the conclude-triage `_fabricated_members` pattern);
  3. counterexample/proof XOR — the certificate must provide exactly one of
     `counterexample` (for 'not_resolved') or `no_counterexample_proof` (for
     'resolved'), and must be coherent with the verdict.

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


def select_finding(issues, pr, pinned=None):
    """Pick the in-scope `[ai-review]` issue for this PR and package the finding.
    When `pinned` (the fix run's pinned_issue) is given, select THAT number exactly —
    per-issue pinning; the leg verifies its own issue, not the globally-highest CLOSED.
    Falls back to highest-CLOSED-scoped (legacy standalone behavior) when pinned is None."""
    pr = str(pr)
    issues = issues if isinstance(issues, list) else []
    # Word-boundary match: `PR #1` must not over-match a body that only says `PR #12`
    # (the trailing `(?!\d)` rejects a longer PR number). Mirrors expand-issues.
    _scope = re.compile(rf"PR #{re.escape(pr)}(?!\d)")
    scoped = [i for i in issues if isinstance(i, dict) and _scope.search(i.get("body") or "")]
    target = None
    if pinned is not None:
        try:
            pin = int(pinned)
        except (TypeError, ValueError):
            pin = None
        if pin is not None:
            target = next((i for i in scoped if i.get("number") == pin), None)
    elif scoped:
        key = lambda i: i.get("number", 0)
        closed = sorted([i for i in scoped if (i.get("state") or "").upper() == "CLOSED"], key=key, reverse=True)
        target = closed[0] if closed else sorted(scoped, key=key, reverse=True)[0]
    if not target:
        return {"issue": None, "state": "none"}
    return {"issue": target.get("number"), "state": (target.get("state") or "").upper(),
            "title": target.get("title", ""), "body": target.get("body", ""),
            "suggested_fix": _suggested_fix(target.get("body", ""))}


_VERDICTS = ("resolved", "not_resolved")


def verdict(cert, diff):
    """Authoritative `fixverify` verdict for a paper-template certificate against the
    committed diff. Returns {check, pass, reason}. Refute-by-default: any structural
    gap, ungrounded premise, or XOR/coherence violation fails."""
    if not isinstance(cert, dict):
        return _bad("no fix-verification certificate produced")

    prem = cert.get("premises")
    if not isinstance(prem, list) or not prem or not all(isinstance(x, str) and x.strip() for x in prem):
        return _bad("certificate has no `premises` (grounded claims about the patch)")
    if not isinstance(cert.get("execution_trace"), str) or not cert["execution_trace"].strip():
        return _bad("certificate missing `execution_trace` (trace of the patched code)")
    v = cert.get("verdict")
    if v not in _VERDICTS:
        return _bad("certificate `verdict` must be 'resolved' or 'not_resolved'")
    if not isinstance(cert.get("concludes_fixed"), bool):
        return _bad("certificate missing `concludes_fixed` conclusion")

    ce = cert.get("counterexample")
    proof = cert.get("no_counterexample_proof")
    has_ce = isinstance(ce, str) and bool(ce.strip())
    has_proof = isinstance(proof, str) and bool(proof.strip())
    if has_ce == has_proof:  # neither, or both
        return _bad("certificate must provide exactly one of `counterexample` / `no_counterexample_proof`")
    if v == "not_resolved" and not has_ce:
        return _bad("verdict 'not_resolved' requires a `counterexample`")
    if v == "resolved" and not has_proof:
        return _bad("verdict 'resolved' requires a `no_counterexample_proof`")
    if cert["concludes_fixed"] != (v == "resolved"):
        return _bad("`concludes_fixed` must agree with `verdict`")

    hay = added_text(diff)
    fabricated = [x for x in prem if _norm(x) not in hay]
    if fabricated:
        return _bad("certificate premises cite code not in the committed diff: "
                    + "; ".join(repr(x) for x in fabricated[:3]))

    trace = _norm(cert["execution_trace"])
    if cert["concludes_fixed"]:
        return {"check": CHECK, "pass": True, "reason": f"fix verified: {trace}"}
    return {"check": CHECK, "pass": False, "reason": f"fix NOT verified: {trace}"}


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

#!/usr/bin/env python3
"""Core proof-carrying verification of a plan safety certificate (stdlib only).

Shared by the post-step (verify-plan-cert.py, which records the verdict into evidence)
and available for reuse. Implements the paper's "tiny checker": re-derive the facts
from plan_ast, then verify the model's certificate (plan_cert) with grounding + closure.
No z3, no toolchain — checking a certificate is a one-pass job.
"""
import re

_SRC_RE = re.compile(r"^(read|fetch|load|get|search|web|tavily)", re.I)
_DEF_SINKS = {"curlpost", "networksend", "writefile", "sendemail", "post", "upload", "curl", "httppost"}
_DEST_ARGS = {"url", "host", "path", "to", "dest", "endpoint"}


def _pairs(cert):
    out = set()
    for p in (cert.get("paths") if isinstance(cert, dict) else None) or []:
        if isinstance(p, list) and len(p) == 2:
            out.add((p[0], p[1]))
        elif isinstance(p, dict):
            out.add((p.get("source"), p.get("var") if "var" in p else p.get("reaches")))
    return out


def verify(plan_ast, plan_cert, params=None):
    """Return the plan_cert_verify verdict dict for a plan_ast + plan_cert."""
    params = params or {}
    if not isinstance(plan_ast, dict) or not isinstance(plan_ast.get("steps"), list):
        return {"verdict": "n/a", "status": "n/a", "reason": "no plan_ast to verify against"}
    if not isinstance(plan_cert, dict) or not isinstance(plan_cert.get("paths"), list):
        return {"verdict": "n/a", "status": "n/a", "reason": "no plan_cert certificate supplied"}

    src_names = {s.lower() for s in params.get("sources", [])}
    sink_names = {s.lower() for s in params.get("sinks", [])} or _DEF_SINKS
    safe_dests = set(params.get("safe_dests", []))

    def is_source(tool):
        t = (tool or "").lower()
        return t in src_names or (not src_names and bool(_SRC_RE.match(t)))

    # 1. re-derive trusted facts from plan_ast
    sources, flows, sink_at = set(), set(), {}
    for s in plan_ast["steps"]:
        if not isinstance(s, dict):
            continue
        tool, result, args = s.get("tool", ""), s.get("result"), (s.get("args") or {})
        for k, v in args.items():
            if isinstance(v, dict) and "$ref" in v and result:
                flows.add((v["$ref"], result))
        if is_source(tool) and result:
            sources.add(result)
        if (tool or "").lower() in sink_names:
            dest = next((v for k, v in args.items()
                         if k.lower() in _DEST_ARGS and isinstance(v, str)), None)
            for k, v in args.items():
                if isinstance(v, dict) and "$ref" in v:
                    sink_at[v["$ref"]] = dest

    claimed = _pairs(plan_cert)

    # 2. verify certificate: grounding + closure
    reasons = []
    for (s, v) in claimed:
        if s not in sources:
            reasons.append(f"ungrounded ({s}->{v}): '{s}' is not a source")
    for s in sources:
        if (s, s) not in claimed:
            reasons.append(f"missing base ({s}->{s})")
    for (s, v) in claimed:
        for (f, t) in flows:
            if f == v and (s, t) not in claimed:
                reasons.append(f"closure gap: ({s}->{v}) + flow({v}->{t}) but ({s}->{t}) omitted")

    valid = not reasons
    # 3. if valid, look for a source->sink leak
    leaks = []
    if valid:
        for (s, v) in claimed:
            if v in sink_at:
                dest = sink_at[v]
                if dest is None or dest not in safe_dests:
                    leaks.append(f"{s} -> sink({v}) @ {dest or 'non-literal-dest'}")

    return {
        "verdict": "pass" if (valid and not leaks) else "fail",
        "status": "ok",
        "certificate_valid": valid,
        "paths_verified": len(claimed),
        "rejected_reasons": reasons[:8],
        "leak": bool(leaks),
        "leaks": leaks[:8],
        "sources": sorted(sources),
        "sinks": sorted(sink_at),
    }

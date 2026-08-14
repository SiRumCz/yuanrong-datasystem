#!/usr/bin/env python3
"""Layer B — advisory divergence: does the declared plan_ast account for the
effectful DISTINCT-TOOL-CALL actions the trusted trajectory shows? Never gates;
explicitly blind to shell-command internals (disclosed in every verdict). Pure,
stdlib-only."""
import os
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actions  # noqa: E402  (sibling engine module)

NOT_INSPECTED = ["shell-command internals"]
_EGRESS_VERBS = {"curlpost", "networksend", "fetch", "webfetch", "post", "httppost"}
_WRITE_VERBS = {"writefile", "write", "edit"}
_READ_VERBS = {"readfile", "read", "readsecret"}


def _literals(args):
    """Concrete string literals declared in a step's args (ignores $ref).
    `args` is agent-derived/untrusted — a malformed shape (e.g. a list
    instead of a dict) must not raise; treat it as declaring nothing."""
    if not isinstance(args, dict):
        return []
    return [v for v in args.values() if isinstance(v, str)]


def _host_of(resource):
    """If `resource` is a scheme-qualified URL (e.g. a Claude WebFetch
    `https://evil.com`), return its host; otherwise None. Lets a plan declare
    the bare host (`evil.com`) while the trajectory record carries the full
    URL — a format difference, not a semantic one."""
    if not isinstance(resource, str) or "://" not in resource:
        return None
    try:
        return urlsplit(resource).netloc or None
    except ValueError:
        return None


def _resource_declared(resource, lits):
    """Conservative resource match: exact string equality, or (egress-only)
    the resource's URL host equals a declared literal."""
    if resource in lits:
        return True
    host = _host_of(resource)
    return host is not None and host in lits


def _step_accounts_for(step, cls, resource):
    tool = step.get("tool")
    # Normalize the declared tool name to bare alphanumerics before matching the
    # verb sets — mirrors the code-review protocol's _toolmap._key() so #249's
    # canonical registry names (network_send/write_file/read_secret) match the
    # same verb sets as the older camelCase forms (networkSend/writeFile/…).
    tool = re.sub(r"[^a-z0-9]", "", tool.lower()) if isinstance(tool, str) else ""
    lits = _literals(step.get("args"))
    if cls == "egress" and tool in _EGRESS_VERBS:
        return _resource_declared(resource, lits)
    if cls == "file_write" and tool in _WRITE_VERBS:
        return resource in lits
    if cls == "secret_read" and tool in _READ_VERBS:
        return resource in lits
    return False


def verify_divergence(plan_ast, trajectory):
    if not isinstance(trajectory, list):
        return {"verdict": "divergent", "undeclared": [], "inspected_channels": [],
                "not_inspected": NOT_INSPECTED + ["(trajectory unavailable — fail-closed)"]}
    steps = (plan_ast or {}).get("steps") if isinstance(plan_ast, dict) else None
    steps = steps if isinstance(steps, list) else []
    in_scope = []
    for rec in trajectory:
        c = actions.classify_action(rec)
        if c is not None:
            in_scope.append(c)
    if not in_scope:
        return {"verdict": "uninspected", "undeclared": [], "inspected_channels": [],
                "not_inspected": NOT_INSPECTED}
    channels = sorted({c["class"] for c in in_scope})
    undeclared = [c for c in in_scope
                  if not any(_step_accounts_for(s, c["class"], c["resource"]) for s in steps if isinstance(s, dict))]
    return {"verdict": "divergent" if undeclared else "pass",
            "undeclared": undeclared, "inspected_channels": channels,
            "not_inspected": NOT_INSPECTED}

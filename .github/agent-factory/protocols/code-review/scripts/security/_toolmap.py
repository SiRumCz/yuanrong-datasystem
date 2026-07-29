#!/usr/bin/env python3
"""Shared tool-name classifier (Python) — the single mapping from any agent's plan_ast
tool names to the guardians/cedar canonical vocabulary. Reads plan-tool-aliases.json
(the one source of truth, also consumed by run-cedar-plan.js).

canonical(tool, args) -> "read_secret" | "read_repo_file" | "read_external" |
                         "network_send" | "write_file" | "run_command" | "compute" | None

A `read` alias resolves to read_secret vs read_repo_file by inspecting the args for a
secret-looking path. compute is a benign pass-through; None means unrecognized (treated
as compute by callers so flow edges survive).
"""
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_D = json.load(open(os.path.join(_HERE, "plan-tool-aliases.json")))
_SECRET = re.compile(_D["secret_path_re"], re.I)
_ALIAS = {a: canon for canon, al in _D["aliases"].items() for a in al}


def _key(tool):
    return re.sub(r"[^a-z0-9]", "", (tool or "").lower())


def canonical(tool, args=None):
    t = _key(tool)
    canon = _ALIAS.get(t)
    if canon is None:  # prefix fallback (readMortgageTerms -> read, curlSomething -> network_send)
        for a, c in _ALIAS.items():
            if t.startswith(a):
                canon = c
                break
    if canon == "read":
        path = " ".join(str(v) for v in (args or {}).values() if isinstance(v, str))
        return "read_secret" if _SECRET.search(path) else "read_repo_file"
    return canon


_ARG = _D.get("arg_aliases", {})


def canonical_args(canon, args):
    """Remap a step's arg keys to the policy's canonical param names for the given
    canonical tool (e.g. network_send: url->host, data->body), preserving values so the
    $ref data-flow — and thus the taint sink params — line up with the policy."""
    rev = {a: param for param, al in _ARG.get(canon, {}).items() for a in al}
    out = {}
    for k, v in (args or {}).items():
        out[rev.get(str(k).lower(), k)] = v
    return out

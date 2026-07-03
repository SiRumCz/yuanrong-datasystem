#!/usr/bin/env python3
"""Shared helpers for duplicate-code-remover checks. Python 3 stdlib only."""
import json
import os

_TRIVIAL = {"", "todo", "tbd", "n/a", "na", "none", "-"}


def emit(check, ok, feedback):
    print(json.dumps({"check": check, "pass": bool(ok), "feedback": feedback}))


def load_evidence(path):
    try:
        with open(path) as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError):
        return None


def params():
    raw = os.environ.get("CHECK_PARAMS", "") or "{}"
    try:
        p = json.loads(raw)
        return p if isinstance(p, dict) else {}
    except ValueError:
        return {}


def non_trivial(s):
    return isinstance(s, str) and s.strip().lower() not in _TRIVIAL

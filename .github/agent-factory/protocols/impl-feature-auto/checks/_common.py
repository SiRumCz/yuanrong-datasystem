#!/usr/bin/env python3
"""Shared helpers for impl-feature-auto checks. Python 3 stdlib only."""
import json
import os

_TRIVIAL = {"", "todo", "tbd", "n/a", "na", "none", "-"}

CONF = {"low": 2, "medium": 1, "high": 0}
BLAST = {"high": 2, "medium": 1, "low": 0}
REV = {"irreversible": 2, "costly": 1, "reversible": 0}


def load_evidence(path):
    try:
        with open(path) as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def NON_TRIVIAL(s):
    return isinstance(s, str) and s.strip().lower() not in _TRIVIAL


def sibling(evidence_path, name):
    p = os.path.join(os.path.dirname(os.path.abspath(evidence_path)), name)
    return p if os.path.isfile(p) else None


def RISK(item):
    """0..6 risk score over the three typed axes (low confidence x high/irreversible)."""
    c = CONF.get(item.get("confidence"), 0)
    b = BLAST.get((item.get("blast_radius") or {}).get("level"), 0)
    r = REV.get((item.get("reversibility") or {}).get("level"), 0)
    return c + b + r

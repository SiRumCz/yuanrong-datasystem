#!/usr/bin/env python3
"""Crypto-verification primitives for the `crypto-verify` phase.

The phase after `fix` asks a simple, deterministic question per fix: *did this
fix actually carry test output, and is the sha256 the agent appended for it
genuine?* An LLM cannot compute sha256, so the hash the agent writes is only a
claim; this module recomputes it for real (Python `hashlib`) so the check can
gate the agent and the conclude hook can post an authoritative comment.

Canonical hash input: the exact `test_output` string, UTF-8, with NO added
trailing newline — so `sha256_hex(out)` equals the agent's
`printf '%s' "$out" | sha256sum`.

Pure + import-only (mirrors the `_diff.py` / `_honesty.py` helper convention),
so it is unit-testable and shared by both `crypto-hash-valid.py` (the check) and
`conclude-crypto-verify.py` (the publish hook).
"""
import hashlib
import os

# The evidence field the agent appends. Spelled with hyphens per the spec.
HASH_FIELD = "crypto-verification-hash"


def sha256_hex(text):
    """sha256 over the exact bytes of `text` (UTF-8, no trailing newline added)."""
    return hashlib.sha256((text if isinstance(text, str) else "").encode("utf-8")).hexdigest()


def _test_output(fix):
    v = fix.get("test_output") if isinstance(fix, dict) else None
    return v if isinstance(v, str) else None


def has_test_output(fix):
    """True iff the fix carries a present, non-empty `test_output` string."""
    out = _test_output(fix)
    return bool(out)  # None or "" -> False


def expected_hash(fix):
    """The sha256 the fix SHOULD carry: hash of test_output, or None when the fix
    has no/empty test_output (an unverifiable fix)."""
    return sha256_hex(_test_output(fix)) if has_test_output(fix) else None


def claimed_hash(fix):
    """The hash the agent actually wrote for this fix (may be str, None, or absent)."""
    return fix.get(HASH_FIELD) if isinstance(fix, dict) else None


def classify(fix, index=0):
    """One fix's authoritative crypto verdict, reused by the check + conclude hook.

    Returns a dict:
      cluster_id, path, index (1-based), has_test_output,
      expected (recomputed hash or None),
      claimed  (agent-written hash or None),
      hash_ok  (claimed matches expected — the honesty gate on the agent),
      verified (green: has test output AND hash_ok)
    """
    cid = fix.get("cluster_id") if isinstance(fix, dict) else None
    path = fix.get("path") if isinstance(fix, dict) else None
    exp = expected_hash(fix)
    got = claimed_hash(fix)
    got_norm = got.lower() if isinstance(got, str) else got
    hash_ok = (got_norm == exp)
    return {
        "cluster_id": cid,
        "path": path,
        "index": index + 1,
        "has_test_output": has_test_output(fix),
        "expected": exp,
        "claimed": got,
        "hash_ok": hash_ok,
        "verified": bool(has_test_output(fix) and hash_ok),
    }


def classify_all(evidence):
    """Classify every fix in the evidence's `fixes[]`. Returns a list of verdicts."""
    fixes = evidence.get("fixes") if isinstance(evidence, dict) else None
    return [classify(f, i) for i, f in enumerate(fixes or []) if isinstance(f, dict)]


def short_id(verdict):
    """A short human identifier for a fix: `<cluster_id> · <basename>`."""
    cid = verdict.get("cluster_id") or f"#{verdict.get('index')}"
    path = verdict.get("path")
    base = os.path.basename(path) if isinstance(path, str) and path else ""
    return f"{cid} · {base}" if base else str(cid)

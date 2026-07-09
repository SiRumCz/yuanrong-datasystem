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
import json
import os
import re

# The evidence field the agent appends. Spelled with hyphens per the spec.
HASH_FIELD = "crypto-verification-hash"

# --- find_test_run: recognize a real test-runner invocation in a trusted
# gh-aw trajectory (agent-stdio.log), by COMMAND shape rather than output
# content. This is a best-effort, tunable heuristic -- NOT a guarantee: a
# project using an unlisted runner, an alias, or a wrapper script will not be
# recognized. Extend this list as new runners are seen in the wild.
TEST_RUNNER_PATTERNS = [
    re.compile(r"\bpytest\b", re.IGNORECASE),
    re.compile(r"\bpython3?\s+-m\s+pytest\b", re.IGNORECASE),
    re.compile(r"\bpython3?\s+-m\s+unittest\b", re.IGNORECASE),
    re.compile(r"\bpython3?\s+\S*test\S*\.py\b", re.IGNORECASE),
    re.compile(r"\bunittest\b", re.IGNORECASE),
    re.compile(r"\bgo\s+test\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+test\b", re.IGNORECASE),
    re.compile(r"\bcargo\s+test\b", re.IGNORECASE),
    re.compile(r"\bctest\b", re.IGNORECASE),
    re.compile(r"\brspec\b", re.IGNORECASE),
    re.compile(r"\bjest\b", re.IGNORECASE),
]


def _is_test_command(command):
    """True iff `command` looks like a test-runner invocation, matched on
    word-ish boundaries so `echo`, `sed`, `cat`, `grep` don't match. See
    TEST_RUNNER_PATTERNS -- this is a heuristic, not a guarantee."""
    if not isinstance(command, str) or not command:
        return False
    return any(p.search(command) for p in TEST_RUNNER_PATTERNS)


def find_test_run(stdio_log):
    """Scan a gh-aw `agent-stdio.log` (mixed plain log lines + JSONL) for the
    newest real test-runner command execution.

    `stdio_log` is the harness's trusted record of what the fix agent actually
    executed -- the agent cannot forge it -- so this verifies a REAL test ran,
    as opposed to the agent merely claiming one did in its own narrative
    output. Keys on the item's `command`, not its `aggregated_output`: e.g. an
    `echo "5 passed"` command does not count no matter what its output says.

    Each JSONL line is parsed independently; lines that aren't valid JSON, or
    that aren't a `{"type":"item.completed","item":{"type":"command_execution",...}}`
    record, are skipped.

    Returns {"ran": bool, "output": str, "exit_code": int|None, "command": str}.
    When multiple test-runner items are present, returns the NEWEST (last in
    file order).
    """
    if not isinstance(stdio_log, str):
        stdio_log = ""

    newest = None
    for line in stdio_log.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("type") != "item.completed":
            continue
        item = record.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        if not _is_test_command(item.get("command")):
            continue
        newest = item  # last match in file order wins -> newest

    if newest is None:
        return {"ran": False, "output": "", "exit_code": None, "command": ""}

    output = newest.get("aggregated_output")
    exit_code = newest.get("exit_code")
    command = newest.get("command")
    return {
        "ran": True,
        "output": output if isinstance(output, str) else "",
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        "command": command if isinstance(command, str) else "",
    }


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

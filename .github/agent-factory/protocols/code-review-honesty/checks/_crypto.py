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
import shlex

# The evidence field the agent appends. Spelled with hyphens per the spec.
HASH_FIELD = "crypto-verification-hash"

# --- find_test_run: recognize a real test-runner invocation in a trusted
# gh-aw trajectory (agent-stdio.log), by the INVOKED PROGRAM (argv[0]) and
# structured args -- never by substring-matching the raw command text, which
# would let a decoy like `echo "pytest run complete: 5 passed"` forge a test
# run. This is a best-effort, tunable heuristic -- NOT a guarantee: a project
# using an unlisted runner, an alias, or a wrapper script will not be
# recognized. Extend these sets as new runners are seen in the wild.
DIRECT_TEST_RUNNER_BASENAMES = {"pytest", "py.test", "ctest", "jest", "rspec", "nosetests"}
PYTHON_BASENAMES = {"python", "python3", "py"}
PYTHON_TEST_MODULES = {"pytest", "unittest", "nose"}
SUBCOMMAND_TEST_RUNNER_BASENAMES = {"go", "npm", "yarn", "pnpm", "cargo"}


def _unwrap_shell(command):
    """If `command` is a `bash`/`sh` `-c`/`-lc` wrapper (e.g.
    `bash -lc "<inner>"`, `/bin/bash -lc '<inner>'`), return the wrapped
    `<inner>` command string. Otherwise return `command` unchanged (including
    when it can't be tokenized, e.g. unbalanced quotes)."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return command
    if (
        len(argv) >= 3
        and os.path.basename(argv[0]) in ("bash", "sh")
        and argv[1] in ("-c", "-lc")
    ):
        return argv[2]
    return command


def _python_invokes_test(args):
    """True iff Python (given `args`, the argv AFTER the interpreter) will
    actually EXECUTE a test module/script -- honoring Python's own CLI
    semantics, where only one of these ever runs: `-m <module>`, `-c
    <code>`/stdin (no script file at all), or the first non-flag positional
    (the script file). Any argv token *after* that point is never reached by
    the interpreter, so e.g. `-c "print(1)" faketest.py` must NOT count --
    `faketest.py` is inert argv text to the inline `-c` snippet, never a file
    Python opens and runs."""
    i, n = 0, len(args)
    while i < n:
        a = args[i]
        if a == "-m":  # python -m <module>: module is the very next token
            mod = args[i + 1] if i + 1 < n else ""
            return mod in PYTHON_TEST_MODULES
        if a in ("-c", "-"):  # inline code / stdin: no script file is executed
            return False
        if a.startswith("-"):  # any other interpreter flag: skip it, keep scanning
            i += 1
            continue
        base = os.path.basename(a)  # first non-flag positional = the script Python runs
        return base.lower().endswith(".py") and "test" in base.lower()
    return False


def _is_test_command(command):
    """True iff `command` actually INVOKES a test runner, decided from the
    invoked program (argv[0]'s basename) and its structured args -- not from
    matching keywords anywhere in the raw text. This means quoted argument
    text (e.g. an `echo "...pytest..."` decoy) can never count, and `pip
    install pytest-mock` / a bare `unittest` mention don't count either.
    See the *_BASENAMES / *_MODULES sets above -- this is a heuristic, not a
    guarantee."""
    if not isinstance(command, str) or not command:
        return False
    inner = _unwrap_shell(command)
    try:
        argv = shlex.split(inner)
    except ValueError:
        return False
    if not argv:
        return False

    prog = os.path.basename(argv[0])

    if prog in DIRECT_TEST_RUNNER_BASENAMES:
        return True

    if prog in PYTHON_BASENAMES:
        # Python's CLI only ever executes ONE of: `-m <module>`, `-c`/stdin
        # (no file), or the first non-flag positional (the script) -- so only
        # that one thing may decide `ran:True`; later argv tokens are inert.
        return _python_invokes_test(argv[1:])

    if prog in SUBCOMMAND_TEST_RUNNER_BASENAMES:
        return len(argv) >= 2 and argv[1] == "test"

    return False


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

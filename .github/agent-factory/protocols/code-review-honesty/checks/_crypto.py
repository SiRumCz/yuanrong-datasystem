#!/usr/bin/env python3
"""Crypto-verification primitives for the honesty fanout's `cryptohash` leg.

The leg asks a simple, deterministic question about ONE test run: *did a real
test actually execute (per the trusted `agent-stdio.log` trajectory, via
`find_test_run`), and is the sha256 the agent appended for its output genuine?*
An LLM cannot compute sha256, so the hash the agent writes is only a claim;
this module recomputes it for real (Python `hashlib`) so the check can gate
the agent and the conclude hook can post an authoritative comment.

Canonical hash input: the exact `test_output` string, UTF-8, with NO added
trailing newline — so `sha256_hex(out)` equals the agent's
`printf '%s' "$out" | sha256sum`.

Pure + import-only (mirrors the `_diff.py` / `_honesty.py` helper convention),
so it is unit-testable and shared by both `crypto-hash-valid.py` (the check) and
`conclude-honesty` (the merge reduce hook).
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


def verify_run(evidence):
    """The single test-run's authoritative crypto verdict, reused by the check
    (`crypto-hash-valid.py`) and the merge reduce hook (`conclude-honesty`).

    `evidence` is the cryptohash leg's single-run shape: `{"ran","command",
    "exit_code","test_output","crypto-verification-hash"}` (see B3a's
    `find_test_run` and the evidence schema). Never trusts the agent's
    self-claimed hash -- always recomputes via `sha256_hex`.

    Returns a dict:
      ran        (bool, from evidence)
      has_output (bool: `test_output` is a present, non-empty string)
      hash_ok    (claimed `crypto-verification-hash` matches the recomputed
                  sha256 of `test_output`; when ran is False or the output is
                  empty there's nothing to hash, so hash_ok is True only if
                  the claimed hash is also null)
      verified   (green: ran AND has_output AND hash_ok)
      reason     ("" when verified; else why not)
    """
    ev = evidence if isinstance(evidence, dict) else {}
    ran = bool(ev.get("ran"))
    output = ev.get("test_output")
    output = output if isinstance(output, str) else ""
    has_output = bool(output)  # "" -> False

    expected = sha256_hex(output) if (ran and has_output) else None
    claimed = ev.get(HASH_FIELD)
    claimed_norm = claimed.lower() if isinstance(claimed, str) else claimed
    hash_ok = (claimed_norm == expected)
    verified = bool(ran and has_output and hash_ok)

    if verified:
        reason = ""
    elif not ran:
        reason = "agent did not run tests"
    elif not has_output:
        reason = "no test output (empty)"
    else:
        reason = "test-output hash invalid (fabricated)"

    return {
        "ran": ran,
        "has_output": has_output,
        "hash_ok": hash_ok,
        "verified": verified,
        "reason": reason,
    }

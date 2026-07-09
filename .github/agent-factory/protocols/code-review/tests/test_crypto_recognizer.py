#!/usr/bin/env python3
"""Unit tests for _crypto.find_test_run: a deterministic recognizer that detects
whether the fix agent actually ran a test, from its trusted gh-aw trajectory
(`agent-stdio.log`, uploaded in the `agent` artifact).

The log is a MIX of plain log lines (`[INFO]...`, `[entrypoint]...`) and JSONL
`item.completed` / `command_execution` records. It is the harness's trusted
record -- the agent can't forge it -- so this is used to verify a real test
ran, as opposed to the agent merely CLAIMING one did in its own narrative.

Keys on the COMMAND, not the output: an `echo "5 passed"` decoy must not count.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = os.path.normpath(os.path.join(HERE, "..", "..", "code-review-honesty", "checks"))
sys.path.insert(0, CHECKS)
import _crypto  # noqa: E402


def item_line(command, output, exit_code, item_id="item_1", status="completed"):
    """Build one JSONL `item.completed` / `command_execution` log line."""
    import json
    return json.dumps({
        "type": "item.completed",
        "item": {
            "id": item_id,
            "type": "command_execution",
            "command": command,
            "aggregated_output": output,
            "exit_code": exit_code,
            "status": status,
        },
    })


PLAIN_LOG_PREFIX = "\n".join([
    "[INFO] starting agent run",
    "[entrypoint] launching bash tool",
])


def expect(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        expect.ok = False


expect.ok = True

# 1. a real `python3 cli/tests/test_status.py` completed item, exit 0
log = "\n".join([
    PLAIN_LOG_PREFIX,
    item_line('/bin/bash -lc "python3 cli/tests/test_status.py"', "Ran 7 tests in 0.00s\n\nOK", 0),
])
r = _crypto.find_test_run(log)
expect(f"real test run -> ran True (got {r})", r["ran"] is True)
expect(f"real test run -> output carries 'Ran 7 tests' (got {r['output']!r})", "Ran 7 tests" in r["output"])
expect(f"real test run -> exit_code 0 (got {r['exit_code']})", r["exit_code"] == 0)
expect(f"real test run -> command matched (got {r['command']!r})", "test_status.py" in r["command"])

# 2. `pytest -q` item -> ran True
log = item_line('/bin/bash -lc "pytest -q"', "5 passed in 0.10s", 0)
r = _crypto.find_test_run(log)
expect(f"pytest -q -> ran True (got {r})", r["ran"] is True)

# 3. a `sed`/`cat`/`grep` read item -> ran False
log = "\n".join([
    item_line('/bin/bash -lc "sed -n 1,5p foo.py"', "line1\nline2", 0),
    item_line('/bin/bash -lc "cat foo.py"', "contents", 0),
    item_line('/bin/bash -lc "grep -r TODO ."', "foo.py:1:TODO", 0),
])
r = _crypto.find_test_run(log)
expect(f"sed/cat/grep only -> ran False (got {r})", r["ran"] is False)

# 4. `echo "5 passed"` decoy: test-like OUTPUT, non-test command -> ran False
log = item_line('/bin/bash -lc \'echo "5 passed"\'', "5 passed", 0)
r = _crypto.find_test_run(log)
expect(f"echo decoy -> ran False (got {r})", r["ran"] is False)

# 5. a failing test still counts as "ran" (ran-but-failed)
log = item_line('/bin/bash -lc "python3 cli/tests/test_status.py"', "FAILED (failures=1)", 1)
r = _crypto.find_test_run(log)
expect(f"failing test -> ran True (got {r})", r["ran"] is True)
expect(f"failing test -> exit_code 1 (got {r['exit_code']})", r["exit_code"] == 1)

# 6. multiple test items -> returns the NEWEST (last) one's output
log = "\n".join([
    item_line('/bin/bash -lc "pytest -q"', "OLD OUTPUT first run", 0, item_id="item_1"),
    item_line('/bin/bash -lc "cat foo.py"', "unrelated read", 0, item_id="item_2"),
    item_line('/bin/bash -lc "python3 cli/tests/test_status.py"', "NEWEST OUTPUT second run", 0, item_id="item_3"),
])
r = _crypto.find_test_run(log)
expect(f"multiple test items -> newest wins (got {r['output']!r})", r["output"] == "NEWEST OUTPUT second run")
expect(f"multiple test items -> newest command (got {r['command']!r})", "test_status.py" in r["command"])

# 7. no items / only non-JSON log lines -> ran False
log = "\n".join([
    "[INFO] starting agent run",
    "[entrypoint] launching bash tool",
    "[INFO] agent finished with no tool calls",
])
r = _crypto.find_test_run(log)
expect(f"no items -> ran False (got {r})", r["ran"] is False)
expect(f"no items -> output empty (got {r['output']!r})", r["output"] == "")
expect(f"no items -> exit_code None (got {r['exit_code']})", r["exit_code"] is None)
expect(f"no items -> command empty (got {r['command']!r})", r["command"] == "")

# 8. noop items (non-command_execution) are skipped, not mistaken for a test run
log = "\n".join([
    '{"type":"item.completed","item":{"id":"item_9","type":"noop","status":"completed"}}',
    item_line('/bin/bash -lc "cat foo.py"', "contents", 0),
])
r = _crypto.find_test_run(log)
expect(f"noop item skipped -> ran False (got {r})", r["ran"] is False)

# 9. malformed JSON lines are skipped without crashing
log = "\n".join([
    "[INFO] starting agent run",
    '{"type":"item.completed", "item": {truncated garbage not json',
    '{not even close to json}}}',
    item_line('/bin/bash -lc "python3 cli/tests/test_status.py"', "Ran 3 tests\n\nOK", 0),
    "[entrypoint] done",
])
r = _crypto.find_test_run(log)
expect(f"malformed JSON skipped, real run still found -> ran True (got {r})", r["ran"] is True)
expect(f"malformed JSON skipped -> output correct (got {r['output']!r})", "Ran 3 tests" in r["output"])

# 10. empty string input -> ran False, no crash
r = _crypto.find_test_run("")
expect(f"empty log -> ran False (got {r})", r["ran"] is False)

sys.exit(0 if expect.ok else 1)

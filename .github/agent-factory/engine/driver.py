"""Step-shaped orchestration core.

Each function invokes an existing engine CLI with the SAME argv/env the
agentic-engine.yml jobs use, so the driver is faithful to production by
construction. There is deliberately NO loop here — the loop lives only in the
local conductor. On GitHub, each function is called once per trust-zone job,
chained by repository_dispatch (one run = one transition).
"""
import json
import os
import subprocess
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent


class DriverError(RuntimeError):
    """A wrapped engine CLI exited non-zero or emitted unparseable output."""


def _run(argv, env=None, expect_json=True, return_stderr=False):
    merged = {**os.environ, **(env or {})}
    r = subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=merged)
    if r.returncode != 0:
        raise DriverError(f"{argv[0]} exited {r.returncode}: {r.stderr.strip()}")
    if return_stderr:
        return r.stderr
    if not expect_json:
        return r.stdout
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise DriverError(f"{argv[0]} emitted non-JSON stdout: {e}\n{r.stdout}") from e


def plan(state_dir, instance, protocol_path, command, head_sha="", node_path="", env=None):
    """Run next.py → parsed action dict. node_path is passed via the NODE_PATH env."""
    argv = [ENGINE_DIR / "next.py", state_dir, instance, protocol_path, command, head_sha]
    return _run(argv, env={**(env or {}), "NODE_PATH": node_path})


def run_checks(protocol_path, state_id, evidence_path, diff_path, files_path,
               node_path="", env=None):
    """Run run-checks.py → parsed verdicts dict. node_path passed via NODE_PATH env."""
    argv = [ENGINE_DIR / "run-checks.py", protocol_path, state_id,
            evidence_path, diff_path, files_path]
    return _run(argv, env={**(env or {}), "NODE_PATH": node_path})


def advance(state_dir, instance, protocol_path, verdicts_path, evidence_path,
            node_path="", env=None):
    """Run advance.py (the sole state writer). Returns STDERR (the dispatch-event +
    ENGINE_LOCAL log stream the conductor routes). Raises DriverError on failure."""
    argv = [ENGINE_DIR / "advance.py", state_dir, instance, protocol_path,
            verdicts_path, evidence_path]
    return _run(argv, env={**(env or {}), "NODE_PATH": node_path}, return_stderr=True)


def join(state_dir, instance, protocol_path, node_path="", env=None):
    """Run join.py (fan-out AND-barrier). Returns STDERR (its dispatch-event stream)."""
    argv = [ENGINE_DIR / "join.py", state_dir, instance, protocol_path]
    return _run(argv, env={**(env or {}), "NODE_PATH": node_path}, return_stderr=True)


def resolve_gate(state_dir, instance, protocol_path, gate_env, node_path="", env=None):
    """Run next.py resolve-gate with the gate decision env (GATE_DECISION/GATE_ACTOR/
    GATE_REASON/GATE_PR_AUTHOR). Returns STDERR (the dispatch-event stream — a mid-
    pipeline gate emits protocol-continue to its next phase; a terminal gate emits none)."""
    argv = [ENGINE_DIR / "next.py", state_dir, instance, protocol_path, "resolve-gate", ""]
    merged = {**(env or {}), "NODE_PATH": node_path, **gate_env}
    return _run(argv, env=merged, return_stderr=True)


def answer_gate(state_dir, instance, protocol_path, answer_body, node_path="", env=None):
    """Run next.py answer with ANSWER_BODY (a data/question gate's `/answer <id>:
    <value>` comment body). Mirrors resolve_gate but for the path-aware answer
    command (do_answer finds the open gate itself via _find_open_gate — it does
    not consume NODE_PATH, but we still thread it for interface consistency with
    the other driver primitives). Returns STDERR (the dispatch-event stream)."""
    argv = [ENGINE_DIR / "next.py", state_dir, instance, protocol_path, "answer", ""]
    merged = {**(env or {}), "NODE_PATH": node_path, "ANSWER_BODY": answer_body}
    return _run(argv, env=merged, return_stderr=True)


def derive_command(protocol_path, event_name, action="", comment_body="", is_pr_comment=""):
    """Pure event→command mapping (auth/instance-keying stay in the YAML ctx step)."""
    if event_name == "workflow_dispatch":
        return "start"
    if event_name == "repository_dispatch":
        # Internal dispatch types are generic; the only type today is protocol-continue.
        return "continue"
    # pull_request / issue_comment → the protocol's triggers block.
    argv = [ENGINE_DIR / "lib.py", "match-trigger", protocol_path,
            event_name, action, comment_body, is_pr_comment]
    return _run(argv, expect_json=False).strip()

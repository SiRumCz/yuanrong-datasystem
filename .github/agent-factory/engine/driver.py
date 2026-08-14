"""Step-shaped orchestration core — the ONE call path to every engine CLI.

Two consumers, one construction. The `_*_spec()` builders produce an engine
CLI's argv + env overlay; the library wrappers (`plan`/`run_checks`/`advance`/
`join`/`resolve_human_task`/`answer_human_task`/`derive_command`) use them for
the local conductor, and the `python3 driver.py <subcommand>` CLI uses the SAME
builders for the four trust-zone jobs in agentic-engine.yml plus protocol-join.yml
and mm-interactive-resume.yml. Neither path can drift from the other, which is
what makes the golden cross-checks (conductor ≡ live) meaningful.

The two execution modes differ deliberately:
  - library `_run()`  — captures stdout/stderr and raises on nonzero, because
    the conductor parses the dispatch-event stderr stream.
  - CLI `_passthrough()` — inherits stdio and propagates the exit code, so a
    workflow step behaves byte-identically to the direct CLI call it replaced
    (`> /tmp/action.json` redirects, `$(...)` captures, the job log, and
    `bash -e` failing the step on a nonzero exit all keep working).

There is deliberately NO loop here — the loop lives only in the local conductor.
On GitHub, each function is called once per trust-zone job, chained by
repository_dispatch (one run = one transition).
"""
import json
import os
import subprocess
import sys
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


# ── The ONE place an engine CLI's argv + env overlay is constructed. Both the
# library wrappers (used by the local conductor) and the CLI below (used by the
# agentic-engine.yml / protocol-join.yml / mm-interactive-resume.yml jobs) build
# their invocation here, so the production path and the offline path cannot
# drift. Each returns (argv, env_overlay); the overlay is applied ON TOP of the
# caller's env, so an explicit NODE_PATH always wins.

def _plan_spec(state_dir, instance, protocol_path, command, head_sha="", node_path=""):
    return ([ENGINE_DIR / "next.py", state_dir, instance, protocol_path,
             command, head_sha],
            {"NODE_PATH": node_path})


def _run_checks_spec(protocol_path, state_id, evidence_path, diff_path, files_path,
                     node_path=""):
    return ([ENGINE_DIR / "run-checks.py", protocol_path, state_id,
             evidence_path, diff_path, files_path],
            {"NODE_PATH": node_path})


def _advance_spec(state_dir, instance, protocol_path, verdicts_path, evidence_path,
                  node_path=""):
    return ([ENGINE_DIR / "advance.py", state_dir, instance, protocol_path,
             verdicts_path, evidence_path],
            {"NODE_PATH": node_path})


def _join_spec(state_dir, instance, protocol_path, node_path=""):
    return ([ENGINE_DIR / "join.py", state_dir, instance, protocol_path],
            {"NODE_PATH": node_path})


def _resolve_human_task_spec(state_dir, instance, protocol_path, human_task_env,
                             node_path=""):
    # The HUMAN_TASK_* decision travels in the ENVIRONMENT: next.py reads it there,
    # and a decision/reason derived from a comment must never reach a command line.
    return ([ENGINE_DIR / "next.py", state_dir, instance, protocol_path,
             "resolve-human-task", ""],
            {"NODE_PATH": node_path, **(human_task_env or {})})


def _answer_human_task_spec(state_dir, instance, protocol_path, answer_body,
                            node_path=""):
    # ANSWER_BODY is an untrusted comment body — environment only, never argv.
    return ([ENGINE_DIR / "next.py", state_dir, instance, protocol_path,
             "answer", ""],
            {"NODE_PATH": node_path, "ANSWER_BODY": answer_body})


def _match_trigger_spec(protocol_path, event_name, action="", comment_body="",
                        is_pr_comment=""):
    return ([ENGINE_DIR / "lib.py", "match-trigger", protocol_path,
             event_name, action, comment_body, is_pr_comment],
            {})


def plan(state_dir, instance, protocol_path, command, head_sha="", node_path="", env=None):
    """Run next.py → parsed action dict. node_path is passed via the NODE_PATH env."""
    argv, overlay = _plan_spec(state_dir, instance, protocol_path, command,
                               head_sha, node_path)
    return _run(argv, env={**(env or {}), **overlay})


def run_checks(protocol_path, state_id, evidence_path, diff_path, files_path,
               node_path="", env=None):
    """Run run-checks.py → parsed verdicts dict. node_path passed via NODE_PATH env."""
    argv, overlay = _run_checks_spec(protocol_path, state_id, evidence_path,
                                     diff_path, files_path, node_path)
    return _run(argv, env={**(env or {}), **overlay})


def advance(state_dir, instance, protocol_path, verdicts_path, evidence_path,
            node_path="", env=None):
    """Run advance.py (the sole state writer). Returns STDERR (the dispatch-event +
    ENGINE_LOCAL log stream the conductor routes). Raises DriverError on failure."""
    argv, overlay = _advance_spec(state_dir, instance, protocol_path,
                                  verdicts_path, evidence_path, node_path)
    return _run(argv, env={**(env or {}), **overlay}, return_stderr=True)


def join(state_dir, instance, protocol_path, node_path="", env=None):
    """Run join.py (fan-out AND-barrier). Returns STDERR (its dispatch-event stream)."""
    argv, overlay = _join_spec(state_dir, instance, protocol_path, node_path)
    return _run(argv, env={**(env or {}), **overlay}, return_stderr=True)


def resolve_human_task(state_dir, instance, protocol_path, human_task_env, node_path="", env=None):
    """Run next.py resolve-human-task with the human task decision env (HUMAN_TASK_DECISION/HUMAN_TASK_ACTOR/
    HUMAN_TASK_REASON/HUMAN_TASK_PR_AUTHOR). Returns STDERR (the dispatch-event stream — a mid-
    pipeline human task emits protocol-continue to its next phase; a terminal human task emits none)."""
    argv, overlay = _resolve_human_task_spec(state_dir, instance, protocol_path,
                                             human_task_env, node_path)
    return _run(argv, env={**(env or {}), **overlay}, return_stderr=True)


def answer_human_task(state_dir, instance, protocol_path, answer_body, node_path="", env=None):
    """Run next.py answer with ANSWER_BODY (a `question` node's `/answer <id>:
    <value>` comment body). Mirrors resolve_human_task but for the path-aware answer
    command (do_answer finds the open human task itself via _find_open_human_task — it does
    not consume NODE_PATH, but we still thread it for interface consistency with
    the other driver primitives). Returns STDERR (the dispatch-event stream)."""
    argv, overlay = _answer_human_task_spec(state_dir, instance, protocol_path,
                                            answer_body, node_path)
    return _run(argv, env={**(env or {}), **overlay}, return_stderr=True)


def derive_command(protocol_path, event_name, action="", comment_body="", is_pr_comment=""):
    """Pure event→command mapping (auth/instance-keying stay in the YAML ctx step)."""
    if event_name == "workflow_dispatch":
        return "start"
    if event_name == "repository_dispatch":
        # Internal dispatch types are generic; the only type today is protocol-continue.
        return "continue"
    # pull_request / issue_comment → the protocol's triggers block.
    argv, _ = _match_trigger_spec(protocol_path, event_name, action,
                                  comment_body, is_pr_comment)
    return _run(argv, expect_json=False).strip()


# ── CLI ────────────────────────────────────────────────────────────────────
# Invoked as `python3 driver.py <subcommand> …` by the workflow jobs. Every
# subcommand builds its invocation from the same _*_spec() the library wrappers
# use, then hands the child our own stdio and exits with the child's code.

_USAGE = """usage: driver.py <subcommand> [args]

  plan               <state_dir> <instance> <protocol.json> <command> [head_sha]
  run-checks         <protocol.json> <state_id> <evidence> <diff> <changed-files>
  advance            <state_dir> <instance> <protocol.json> <verdicts> <evidence>
  join               <state_dir> <instance> <protocol.json>
  answer-human-task  <state_dir> <instance> <protocol.json>
  resolve-human-task <state_dir> <instance> <protocol.json>
  derive-command     <protocol.json> <event> [action] [body] [is_pr_comment]

NODE_PATH is read from the environment. So are the untrusted values: ANSWER_BODY
and HUMAN_TASK_DECISION/HUMAN_TASK_ACTOR/HUMAN_TASK_REASON/HUMAN_TASK_PR_AUTHOR
(never passed on a command line).
"""

_HUMAN_TASK_ENV_KEYS = ("HUMAN_TASK_DECISION", "HUMAN_TASK_ACTOR",
                        "HUMAN_TASK_REASON", "HUMAN_TASK_PR_AUTHOR")


def _passthrough(spec):
    """Run the spec's CLI with OUR stdio (no capture) and return its exit code.
    Inheriting fd 1/2 is what makes a rewired workflow step byte-identical: the
    child writes to whatever the step redirected or captured, and its stderr
    lands in the job log exactly as before."""
    argv, overlay = spec
    r = subprocess.run([str(a) for a in argv], env={**os.environ, **overlay})
    return r.returncode


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(_USAGE)
        return 2
    sub, rest = args[0], args[1:]
    node_path = os.environ.get("NODE_PATH", "")

    def _arity(lo, hi):
        if not (lo <= len(rest) <= hi):
            sys.stderr.write(f"driver.py {sub}: expected {lo}..{hi} args, got {len(rest)}\n")
            sys.stderr.write(_USAGE)
            return False
        return True

    if sub == "plan":
        if not _arity(4, 5):
            return 2
        state_dir, instance, proto, command = rest[:4]
        head_sha = rest[4] if len(rest) > 4 else ""
        return _passthrough(_plan_spec(state_dir, instance, proto, command,
                                       head_sha, node_path))

    if sub == "run-checks":
        if not _arity(5, 5):
            return 2
        return _passthrough(_run_checks_spec(*rest, node_path=node_path))

    if sub == "advance":
        if not _arity(5, 5):
            return 2
        return _passthrough(_advance_spec(*rest, node_path=node_path))

    if sub == "join":
        if not _arity(3, 3):
            return 2
        return _passthrough(_join_spec(*rest, node_path=node_path))

    if sub == "answer-human-task":
        if not _arity(3, 3):
            return 2
        state_dir, instance, proto = rest
        return _passthrough(_answer_human_task_spec(
            state_dir, instance, proto,
            os.environ.get("ANSWER_BODY", ""), node_path))

    if sub == "resolve-human-task":
        if not _arity(3, 3):
            return 2
        state_dir, instance, proto = rest
        # The decision/actor/reason are read HERE, from the environment — the
        # subcommand takes no positional for them (they are comment-derived).
        human_task_env = {k: os.environ[k] for k in _HUMAN_TASK_ENV_KEYS
                          if k in os.environ}
        return _passthrough(_resolve_human_task_spec(
            state_dir, instance, proto, human_task_env, node_path))

    if sub == "derive-command":
        if not _arity(2, 5):
            return 2
        try:
            sys.stdout.write(derive_command(*rest) + "\n")
        except DriverError as exc:
            sys.stderr.write(f"[driver] {exc}\n")
            return 1
        return 0

    sys.stderr.write(f"driver.py: unknown subcommand {sub!r}\n")
    sys.stderr.write(_USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())

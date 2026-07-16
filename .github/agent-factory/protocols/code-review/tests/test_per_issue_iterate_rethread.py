#!/usr/bin/env python3
"""Regression test: the continue/ITERATE re-dispatch of a per-issue leg re-threads
its delivered issue item (next.py continue-onto-agent path).

The per-issue dynamic fanout threads each leg's issue into the FIRST dispatch via
legs[].inputs (see test_per_issue_fanout Part B). But an ITERATE/continue re-dispatch
of a leg's `triage` — which declares NO `from:` inputs — previously emitted a
run-agent action with NO inputs, so agentic-engine.yml fell back to an empty
aw_context.inputs. The codex triage agent then could not tell which [ai-review] issue
it owned and noop'd; its evidence check failed and the iterate loop could never
converge (burned every max_iteration). This drives the REAL next.py:

  Step 1  enter `per-issue`     -> run-fanout, stages each leg's <as>.item.json + commits
  Step 2  continue that leg's `triage` (an iterate hop) -> the emitted run-agent action
          must RE-CARRY the staged issue as an input spec {as: issue, path: ...}.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "..", "..", "engine")
NEXT = os.path.join(ENGINE, "next.py")
PROTO = os.path.join(HERE, "..", "protocol.json")
GIT_ID = ["-c", "user.email=test@engine", "-c", "user.name=engine-test"]
PID = "code-review"
failures = []


def ok(name, cond):
    if not cond:
        failures.append(name)


def git(cwd, *a, id_=False):
    subprocess.run(["git", "-C", cwd] + (GIT_ID if id_ else []) + list(a),
                   check=True, capture_output=True, text=True)


def run_next(work, instance, node_path, remote):
    env = dict(os.environ, STATE_REMOTE=remote, STATE_BRANCH="agentic-state",
               NODE_PATH=node_path, ENGINE_LOCAL="1")
    return subprocess.run(
        [sys.executable, NEXT, work, instance, PROTO, "continue", "deadbeef"],
        env=env, capture_output=True, text=True)


def last_action(stdout, kind):
    act = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                cand = json.loads(line)
            except Exception:
                continue
            if cand.get("action") == kind:
                act = cand
    return act


with tempfile.TemporaryDirectory() as tmp:
    instance = "pr-iterate"
    bare = os.path.join(tmp, f"{instance}.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    seed = os.path.join(tmp, "seed")
    subprocess.run(["git", "init", "-q", seed], check=True)
    subprocess.run(["git", "-C", seed, "symbolic-ref", "HEAD", "refs/heads/agentic-state"], check=True)
    inst = os.path.join(seed, PID, instance)
    os.makedirs(inst)
    with open(os.path.join(inst, "_instance.yaml"), "w") as f:
        f.write(f"protocol: {PID}\ninstance: {instance}\nhead_sha: deadbeef\nphase: per-issue\njoined: false\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed", id_=True)
    subprocess.run(["git", "-C", seed, "remote", "add", "origin", bare], check=True)
    git(seed, "push", "-q", "-u", "origin", "agentic-state")

    # Step 1: enter the per-issue fanout — stages each leg's issue item + commits.
    p1 = run_next(os.path.join(tmp, "w1"), instance, "per-issue", bare)
    fan = last_action(p1.stdout, "run-fanout")
    ok("[1] fan-out emitted", fan is not None)
    if fan is None:
        print("STEP1 STDOUT:\n", p1.stdout, "\nSTEP1 STDERR:\n", p1.stderr)
    # ENGINE_LOCAL expand-issues delivers issues 101 & 102; leg id = sha1(number)[:8].
    leg = hashlib.sha1(b"101").hexdigest()[:8]

    # Step 2: iterate/continue re-dispatch of that leg's triage sub-state.
    p2 = run_next(os.path.join(tmp, "w2"), instance, f"per-issue.{leg}.triage", bare)
    ok("[2] continue exits 0", p2.returncode == 0)
    act = last_action(p2.stdout, "run-agent")
    ok("[2] emits run-agent", act is not None)
    if act is None:
        print("STEP2 STDOUT:\n", p2.stdout, "\nSTEP2 STDERR:\n", p2.stderr)
    else:
        ok("[2] action path is the triage leg", act.get("path") == f"per-issue.{leg}.triage")
        specs = act.get("inputs") or []
        issue_specs = [s for s in specs if isinstance(s, dict) and s.get("as") == "issue"]
        ok("[2] run-agent RE-CARRIES the issue input (the fix)", len(issue_specs) == 1)
        if issue_specs:
            p = issue_specs[0].get("path", "")
            ok("[2] input path exists in the state checkout", os.path.isfile(p))
            ok("[2] input item is the leg's issue (#101)",
               os.path.isfile(p) and json.load(open(p)).get("number") == 101)

if failures:
    print("FAIL test_per_issue_iterate_rethread:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - continue/iterate re-dispatch re-threads the leg's staged issue item")

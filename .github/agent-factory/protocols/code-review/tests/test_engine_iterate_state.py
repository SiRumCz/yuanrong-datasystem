#!/usr/bin/env python3
"""Regression test for the iterate state-reset bug in the unified engine.

When an agent phase fails its gate, advance.py records the failed iteration
(iteration++, history += {iteration, checks, feedback}) and re-dispatches a
`protocol-continue` for the SAME phase. The re-dispatched `continue` runs next.py,
whose continue→agent arm calls enter_node(); enter_node's agent arm USED TO
re-seed the per-phase state file unconditionally ({iteration: 1, history: []}),
clobbering the iterate state advance.py had just written. That:
  - reset the iteration counter (defeating the `iter_ < max_iter` exhaustion
    guard — a persistently-failing gate would re-dispatch forever),
  - wiped the failed-iteration history (the status comment, a pure projection of
    `history`, then under-reported the retry), and
  - dropped the failure feedback the retried agent needs.

Both scenarios drive the REAL next.py as a subprocess (matching the suite's
subprocess convention) against a local bare git repo standing in for the
agentic-state remote:
  - ITERATE re-entry  → must PRESERVE state and thread iteration + feedback;
  - FIRST entry       → must still seed {iteration 1, history []}, publish it,
                        and emit iteration 1 / empty feedback (no regression).

Post Task 8, `fix` is no longer a top-level phase — it is the second sub-state of
each `per-issue` fanout leg (`per-issue.<leg_id>.fix`). We drive it at that
relocated coordinate so the bounded-iterate guarantee is proven to survive the
move into the fanout. A leg's state file carries `state: <enclosing fanout id>`
(the leg's life-state, here `per-issue`), not the leaf id.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml  # PyYAML is a declared runtime dep of the engine

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "..", "..", "engine")
NEXT = os.path.join(ENGINE, "next.py")
PROTO = os.path.join(HERE, "..", "protocol.json")
GIT_ID = ["-c", "user.email=test@engine", "-c", "user.name=engine-test"]
FB = "iteration-1 failed: fix-schema-valid not satisfied (FEEDBACK_MARKER)"
# Any 8-hex leg id resolves against the per-issue `each` template; use sha1('101')[:8]
# (the leg the ENGINE_LOCAL items.json #101 issue would materialize) for realism.
LEG = "dbc0f004"
FIX_PATH = f"per-issue.{LEG}.fix"
FIX_FILE = f"per-issue.{LEG}.fix.yaml"

failures = []


def ok(name, cond):
    if not cond:
        failures.append(name)


def git(cwd, *args, id_=False):
    cmd = ["git", "-C", cwd] + (GIT_ID if id_ else []) + list(args)
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def setup_remote(tmp, instance, fix_state):
    """Bare agentic-state remote with code-review/<instance>/_instance.yaml at the
    `per-issue` phase, and the leg's per-issue.<leg>.fix.yaml seeded from
    `fix_state` (or omitted when None, to model a never-yet-entered leg sub-state)."""
    bare = os.path.join(tmp, f"{instance}.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    seed = os.path.join(tmp, f"{instance}-seed")
    subprocess.run(["git", "init", "-q", seed], check=True)
    subprocess.run(["git", "-C", seed, "symbolic-ref", "HEAD",
                    "refs/heads/agentic-state"], check=True)
    inst = os.path.join(seed, "code-review", instance)
    os.makedirs(inst)
    with open(os.path.join(inst, "_instance.yaml"), "w") as f:
        f.write(f"protocol: code-review\ninstance: {instance}\n"
                "head_sha: deadbeef\nphase: per-issue\njoined: false\n")
    if fix_state is not None:
        with open(os.path.join(inst, FIX_FILE), "w") as f:
            yaml.safe_dump(fix_state, f, sort_keys=False)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed", id_=True)
    subprocess.run(["git", "-C", seed, "remote", "add", "origin", bare], check=True)
    git(seed, "push", "-q", "-u", "origin", "agentic-state")
    return bare


def run_continue(tmp, bare, instance):
    work = os.path.join(tmp, f"{instance}-work")  # must NOT exist; cloned into
    env = dict(os.environ, STATE_REMOTE=bare, STATE_BRANCH="agentic-state",
               NODE_PATH=FIX_PATH, ENGINE_LOCAL="1")
    r = subprocess.run([sys.executable, NEXT, work, instance, PROTO, "continue", "deadbeef"],
                       env=env, capture_output=True, text=True)
    return work, r


def parse_action(stdout):
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("action") == "run-agent":
                return j
    return None


def read_state(work, instance):
    fp = os.path.join(work, "code-review", instance, FIX_FILE)
    return yaml.safe_load(open(fp)) if os.path.isfile(fp) else None


# ── Scenario A: iterate re-entry preserves state + threads feedback ──
with tempfile.TemporaryDirectory() as tmp:
    bare = setup_remote(tmp, "pr-iter", fix_state={
        "protocol": "code-review", "instance": "pr-iter", "state": "per-issue",
        "iteration": 2, "gates": {}, "head_sha": "deadbeef",
        "history": [{"iteration": 1, "agent_run_id": "111",
                     "checks": {"fix-schema-valid": "fail"}, "feedback": FB}],
    })
    work, r = run_continue(tmp, bare, "pr-iter")

    ok("[iterate] next.py exits 0 (no empty-commit failure on re-entry)",
       r.returncode == 0)
    act = parse_action(r.stdout)
    ok("[iterate] emitted a run-agent action", act is not None)
    if act is not None:
        ok("[iterate] run-agent carries the real iteration (2, not reset to 1)",
           act.get("iteration") == 2)
        ok("[iterate] run-agent threads the iteration-1 failure feedback",
           FB in (act.get("feedback") or ""))
    st = read_state(work, "pr-iter") or {}
    ok("[iterate] state file preserved iteration: 2", st.get("iteration") == 2)
    hist = st.get("history") or []
    ok("[iterate] state file preserved the iteration-1 history entry",
       len(hist) == 1 and hist[0].get("iteration") == 1)

# ── Scenario B: first entry still seeds + publishes (no regression) ──
with tempfile.TemporaryDirectory() as tmp:
    bare = setup_remote(tmp, "pr-fresh", fix_state=None)
    work, r = run_continue(tmp, bare, "pr-fresh")

    ok("[first-entry] next.py exits 0", r.returncode == 0)
    act = parse_action(r.stdout)
    ok("[first-entry] emitted a run-agent action", act is not None)
    if act is not None:
        ok("[first-entry] run-agent iteration is 1", act.get("iteration") == 1)
        ok("[first-entry] run-agent feedback is empty", (act.get("feedback") or "") == "")
    st = read_state(work, "pr-fresh") or {}
    ok("[first-entry] seeded fresh state (iteration 1, empty history)",
       st.get("iteration") == 1 and (st.get("history") or []) == [])
    # the seed was published (re-clone origin and confirm the leg fix state landed)
    check = os.path.join(tmp, "verify")
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", bare, check], check=True)
    ok("[first-entry] seed was published to origin",
       os.path.isfile(os.path.join(check, "code-review", "pr-fresh", FIX_FILE)))

if failures:
    print("FAIL test_engine_iterate_state:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - engine iterate preserves state + threads feedback; first entry seeds + publishes")

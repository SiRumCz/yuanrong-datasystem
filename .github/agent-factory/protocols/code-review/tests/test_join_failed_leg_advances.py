#!/usr/bin/env python3
"""Regression: a top fan-out phase with a FAILED leg must ADVANCE to the join's
`.next` (preflight → preflight-gate), not dead-end.

Before the fix, join.py main()'s policy-not-satisfied path finalized the instance
(joined:true) WITHOUT dispatching a continue — so for a MULTI-PHASE pipeline a single
failed dimension left the instance joined-but-not-advanced: a permanent WEDGE, with
no gate for a human to `/override`. The gate is where a "could-not-verify" dimension
should surface (it reads `from: adherence` → None → blocks → /override).

Drives the REAL join.py against the REAL code-review protocol on a bare agentic-state
remote (ENGINE_LOCAL: dispatches echo to stderr, no network).
"""
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "..", "..", "engine")
JOIN = os.path.join(ENGINE, "join.py")
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


with tempfile.TemporaryDirectory() as tmp:
    instance = "pr-failleg"
    bare = os.path.join(tmp, f"{instance}.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    seed = os.path.join(tmp, "seed")
    subprocess.run(["git", "init", "-q", seed], check=True)
    subprocess.run(["git", "-C", seed, "symbolic-ref", "HEAD", "refs/heads/agentic-state"], check=True)
    inst = os.path.join(seed, PID, instance)
    os.makedirs(inst)
    with open(os.path.join(inst, "_instance.yaml"), "w") as f:
        f.write(f"protocol: {PID}\ninstance: {instance}\nhead_sha: deadbeef\nphase: preflight\njoined: false\n")
    # 4 preflight dimension cursors: adherence FAILED, the rest done → 3/4 done, so
    # policy 'all' is NOT satisfied (the wedge trigger).
    for dim, st in [("adherence", "failed"), ("mm-compliance", "done"),
                    ("consistency", "done"), ("security", "done")]:
        with open(os.path.join(inst, f"preflight.{dim}.yaml"), "w") as f:
            f.write(f"protocol: {PID}\ninstance: {instance}\nstate: {st}\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed", id_=True)
    subprocess.run(["git", "-C", seed, "remote", "add", "origin", bare], check=True)
    git(seed, "push", "-q", "-u", "origin", "agentic-state")

    work = os.path.join(tmp, "work")
    env = dict(os.environ, STATE_REMOTE=bare, STATE_BRANCH="agentic-state",
               NODE_PATH="", ENGINE_LOCAL="1", PR=instance, PR_HEAD_SHA="deadbeef",
               GITHUB_REPOSITORY="test/repo")
    r = subprocess.run([sys.executable, JOIN, work, instance, PROTO],
                       env=env, capture_output=True, text=True)
    ok("[failed-leg] join.py exits 0", r.returncode == 0)
    if r.returncode != 0:
        print("STDERR tail:\n", r.stderr[-1500:])

    combined = r.stdout + r.stderr
    # THE FIX: advance to preflight-gate via protocol-continue, not finalize-in-place.
    ok("[failed-leg] dispatches protocol-continue (advance, not wedge)",
       "protocol-continue" in combined)
    ok("[failed-leg] advances specifically to preflight-gate",
       "preflight-gate" in combined)

    chk = os.path.join(tmp, "verify")
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", bare, chk], check=True)
    inst_data = yaml.safe_load(open(os.path.join(chk, PID, instance, "_instance.yaml")))
    ok("[failed-leg] instance joined", inst_data.get("joined") is True)
    ok("[failed-leg] phase advanced to preflight-gate (not stuck at preflight)",
       inst_data.get("phase") == "preflight-gate")

if failures:
    print("FAIL test_join_failed_leg_advances:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - a failed fan-out leg advances to the gate instead of wedging")

#!/usr/bin/env python3
"""A 0-leg dynamic fanout must NOT emit a run-fanout with empty legs (every GHA
zone gates on legs != '[]' and skips, so the enclosing join never fires and the
instance stalls). next.py fires the fanout's own protocol-join and emits a noop,
letting the join advance to the fanout's .next (honesty-gate -> done on a clean PR).

Drives the REAL next.py as a subprocess against a bare agentic-state remote."""
import json, os, subprocess, sys, tempfile
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "..", "..", "engine")
NEXT = os.path.join(ENGINE, "next.py")
PROTO = os.path.join(HERE, "fixtures", "emptyfan", "protocol.json")
GIT_ID = ["-c", "user.email=test@engine", "-c", "user.name=engine-test"]
PID = "emptyfan-fixture"
failures = []
def ok(name, cond):
    if not cond: failures.append(name)
def git(cwd, *a, id_=False):
    subprocess.run(["git", "-C", cwd] + (GIT_ID if id_ else []) + list(a), check=True, capture_output=True, text=True)

with tempfile.TemporaryDirectory() as tmp:
    instance = "pr-empty"
    bare = os.path.join(tmp, f"{instance}.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    seed = os.path.join(tmp, "seed")
    subprocess.run(["git", "init", "-q", seed], check=True)
    subprocess.run(["git", "-C", seed, "symbolic-ref", "HEAD", "refs/heads/agentic-state"], check=True)
    inst = os.path.join(seed, PID, instance); os.makedirs(inst)
    with open(os.path.join(inst, "_instance.yaml"), "w") as f:
        f.write(f"protocol: {PID}\ninstance: {instance}\nhead_sha: deadbeef\nphase: per-issue\njoined: false\n")
    git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "seed", id_=True)
    subprocess.run(["git", "-C", seed, "remote", "add", "origin", bare], check=True)
    git(seed, "push", "-q", "-u", "origin", "agentic-state")

    work = os.path.join(tmp, "work")
    env = dict(os.environ, STATE_REMOTE=bare, STATE_BRANCH="agentic-state",
               NODE_PATH="per-issue", ENGINE_LOCAL="1")
    r = subprocess.run([sys.executable, NEXT, work, instance, PROTO, "continue", "deadbeef"],
                       env=env, capture_output=True, text=True)

    ok("[empty-fanout] next.py exits 0", r.returncode == 0)
    act = None
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try: act = json.loads(line)
            except Exception: pass
    ok("[empty-fanout] emits a noop, not a run-fanout",
       act is not None and act.get("action") == "noop"
       and act.get("reason") == "empty-fanout:per-issue")
    ok("[empty-fanout] never emits run-fanout with empty legs", '"run-fanout"' not in r.stdout)
    ok("[empty-fanout] dispatches the fanout's protocol-join",
       "event_type=protocol-join" in r.stderr)
    # the 0-leg manifest was materialized + pushed (join.py will read it as vacuous)
    check = os.path.join(tmp, "verify")
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", bare, check], check=True)
    man = os.path.join(check, PID, instance, "per-issue.__manifest.yaml")
    ok("[empty-fanout] 0-leg manifest persisted", os.path.isfile(man)
       and (yaml.safe_load(open(man)) or {}).get("count") == 0)

if failures:
    print("FAIL test_engine_empty_fanout:")
    for f in failures: print(" -", f)
    sys.exit(1)
print("OK - empty dynamic fanout fires its join and emits a noop")

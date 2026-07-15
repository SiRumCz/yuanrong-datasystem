#!/usr/bin/env python3
"""Option-B top-merge gate: the finalize arm must honor blocked/on_blocked/.next.
  - blocked:true + on_blocked:halt  → write the `halted` marker do_override reads,
    fail the check-run, DO NOT continue.
  - not blocked + .next             → dispatch_continue onto .next (no finalize).
  - not blocked + no .next          → finalize (joined:true) via finalize_merge_result.
Drives the REAL next.py against a bare agentic-state remote (ENGINE_LOCAL)."""
import json, os, subprocess, sys, tempfile
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "..", "..", "engine")
NEXT = os.path.join(ENGINE, "next.py")
GIT_ID = ["-c", "user.email=test@engine", "-c", "user.name=engine-test"]
failures = []
def ok(name, cond):
    if not cond: failures.append(name)
def git(cwd, *a, id_=False):
    subprocess.run(["git", "-C", cwd] + (GIT_ID if id_ else []) + list(a), check=True, capture_output=True, text=True)

def drive(proto_path, pid, instance, mode):
    """Seed a bare remote at phase honesty-gate, run next.py continue onto the
    merge, return (result, re-cloned _instance.yaml dict)."""
    tmp = tempfile.mkdtemp()
    bare = os.path.join(tmp, f"{instance}.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    seed = os.path.join(tmp, "seed"); subprocess.run(["git", "init", "-q", seed], check=True)
    subprocess.run(["git", "-C", seed, "symbolic-ref", "HEAD", "refs/heads/agentic-state"], check=True)
    inst = os.path.join(seed, pid, instance); os.makedirs(inst)
    with open(os.path.join(inst, "_instance.yaml"), "w") as f:
        f.write(f"protocol: {pid}\ninstance: {instance}\nhead_sha: deadbeef\nphase: honesty-gate\njoined: false\n")
    git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "seed", id_=True)
    subprocess.run(["git", "-C", seed, "remote", "add", "origin", bare], check=True)
    git(seed, "push", "-q", "-u", "origin", "agentic-state")
    work = os.path.join(tmp, "work")
    env = dict(os.environ, STATE_REMOTE=bare, STATE_BRANCH="agentic-state",
               NODE_PATH="honesty-gate", ENGINE_LOCAL="1", GATE_STUB_MODE=mode)
    r = subprocess.run([sys.executable, NEXT, work, instance, proto_path, "continue", "deadbeef"],
                       env=env, capture_output=True, text=True)
    chk = os.path.join(tmp, "verify")
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", bare, chk], check=True)
    inst_data = yaml.safe_load(open(os.path.join(chk, pid, instance, "_instance.yaml")))
    return r, inst_data

GATE = os.path.join(HERE, "fixtures", "gate", "protocol.json")
NONEXT = os.path.join(HERE, "fixtures", "gate-nonext", "protocol.json")

# --- Scenario 1: blocked + on_blocked:halt → halt, no continue ---
r, inst = drive(GATE, "gate-fixture", "pr-blk", "blocked")
ok("[blocked] next.py exits 0", r.returncode == 0)
ok("[blocked] halted marker written for the gate phase",
   (inst.get("halted") or {}).get("reason") == "blocked"
   and inst["halted"].get("phase") == "honesty-gate")
ok("[blocked] did NOT dispatch a continue past the gate",
   "event_type=protocol-continue" not in r.stderr)
# Tight: the actual `blocked` phase-label emission (ensure_phase_label under
# ENGINE_LOCAL logs `[ENGINE_LOCAL] phase-label <inst>: <prev> → ⛔ blocked`), not
# the check-run *title* line (`title=Gate blocked`) which any 'blocked' substring hits.
ok("[blocked] emits the blocked phase-label (not just the check-run title)",
   any(ln.startswith("[ENGINE_LOCAL] phase-label") and ln.rstrip().endswith("⛔ blocked")
       for ln in r.stderr.splitlines()))

# --- Scenario 2: clear + .next → continue to post-fix, no halt ---
r, inst = drive(GATE, "gate-fixture", "pr-clr", "clear")
ok("[clear] next.py exits 0", r.returncode == 0)
ok("[clear] no halted marker", "halted" not in inst)
ok("[clear] advanced the cursor to post-fix", inst.get("phase") == "post-fix")
ok("[clear] dispatched a continue onto post-fix",
   "event_type=protocol-continue" in r.stderr and "post-fix" in r.stderr)

# --- Scenario 3: clear + no .next → finalize (joined) ---
r, inst = drive(NONEXT, "gate-nonext-fixture", "pr-fin", "clear")
ok("[finalize] next.py exits 0", r.returncode == 0)
ok("[finalize] no halted marker", "halted" not in inst)
ok("[finalize] instance joined", inst.get("joined") is True)
ok("[finalize] did NOT dispatch a continue", "event_type=protocol-continue" not in r.stderr)

# --- Scenario 4: crash — the gate hook exits NONZERO → run_merge_hook returns a
#     NEUTRAL res with NO `blocked` key. A gate with on_blocked:halt must FAIL CLOSED:
#     halt on the non-genuine verdict, NOT fall through to .next. Regression guard for
#     the fail-open (RED before the crashed/neutral halt fix, GREEN after). ---
r, inst = drive(GATE, "gate-fixture", "pr-crash", "crash")
ok("[crash] next.py exits 0", r.returncode == 0)
ok("[crash] halted marker written for the gate phase",
   (inst.get("halted") or {}).get("reason") == "blocked"
   and inst["halted"].get("phase") == "honesty-gate")
ok("[crash] did NOT advance to post-fix", inst.get("phase") != "post-fix")
ok("[crash] did NOT dispatch a continue past the gate",
   "event_type=protocol-continue" not in r.stderr)

# --- Scenario 5: genuine failure — the reducer returns {conclusion:'failure',
#     blocked:False}. A gate with on_blocked:halt must FAIL CLOSED: a genuine failure
#     verdict halts, NOT falls through to .next. Regression guard for the fail-open
#     (RED before the failure-halt fix, GREEN after). ---
r, inst = drive(GATE, "gate-fixture", "pr-fail", "fail")
ok("[fail] next.py exits 0", r.returncode == 0)
ok("[fail] halted marker written for the gate phase",
   (inst.get("halted") or {}).get("reason") == "blocked"
   and inst["halted"].get("phase") == "honesty-gate")
ok("[fail] did NOT advance to post-fix", inst.get("phase") != "post-fix")
ok("[fail] did NOT dispatch a continue past the gate",
   "event_type=protocol-continue" not in r.stderr)

if failures:
    print("FAIL test_engine_top_merge_gate:")
    for f in failures: print(" -", f)
    sys.exit(1)
print("OK - top-merge gate honors blocked/on_blocked/.next")

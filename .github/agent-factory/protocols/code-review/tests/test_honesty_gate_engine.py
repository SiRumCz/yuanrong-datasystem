#!/usr/bin/env python3
"""Task 10 end-to-end: drive the REAL next.py `continue` onto the top `honesty-gate`
merge over the REAL code-review protocol + REAL aggregate-honesty hook, and assert
the engine's Option-B top-merge arm (Task 5) reacts to the reducer's `blocked`.

Seeds a bare agentic-state remote at phase `honesty-gate` with the per-issue dynamic
fanout ALREADY materialized: a 2-leg `per-issue.__manifest.yaml`, and each leg's
terminal `honesty-verdict` output evidence (conclude-honesty's {conclusion,summary}).
run_merge_hook then folds those rows through aggregate-honesty (from_fanout:'per-issue')
into {conclusion,summary,blocked,rollup}, and the top-merge arm halts-or-continues:

  - ANY dishonest leg  -> aggregate-honesty blocked:true + on_blocked:halt
                          -> `halted` marker written, NO continue past the gate.
  - ALL honest legs    -> blocked:false + .next:'post-fix'
                          -> phase advanced to post-fix, a protocol-continue dispatched.

This ties Task 10's wiring (aggregate-honesty + honesty-gate + from_fanout:per-issue)
to Task 5's engine arm; the pure-ABI folding is covered by test_aggregate_honesty.py."""
import json, os, subprocess, sys, tempfile
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "engine"))
NEXT = os.path.join(ENGINE, "next.py")
PROTO = os.path.normpath(os.path.join(HERE, "..", "protocol.json"))     # REAL protocol
GIT_ID = ["-c", "user.email=test@engine", "-c", "user.name=engine-test"]
PID = "code-review"
failures = []

sys.path.insert(0, ENGINE)
import lib  # noqa: E402


def ok(name, cond):
    if not cond:
        failures.append(name)


def git(cwd, *a, id_=False):
    subprocess.run(["git", "-C", cwd] + (GIT_ID if id_ else []) + list(a),
                   check=True, capture_output=True, text=True)


def drive(instance, verdicts):
    """verdicts: [{number, conclusion, summary}] — one per per-issue leg. Seed the
    per-issue manifest + each leg's honesty-verdict evidence, then run next.py
    `continue` onto honesty-gate. Return (proc, re-cloned _instance.yaml dict,
    verify-checkout dir) — the checkout lets a caller inspect persisted evidence."""
    proto = json.load(open(PROTO))
    items = [{"number": v["number"]} for v in verdicts]
    man = lib.build_manifest(items, "$.number", 32)     # production leg-id shape
    tmp = tempfile.mkdtemp()
    bare = os.path.join(tmp, f"{instance}.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    seed = os.path.join(tmp, "seed")
    subprocess.run(["git", "init", "-q", seed], check=True)
    subprocess.run(["git", "-C", seed, "symbolic-ref", "HEAD", "refs/heads/agentic-state"], check=True)
    inst = os.path.join(seed, PID, instance)
    os.makedirs(inst)
    with open(os.path.join(inst, "_instance.yaml"), "w") as f:
        f.write(f"protocol: {PID}\ninstance: {instance}\nhead_sha: deadbeef\nphase: honesty-gate\njoined: false\n")
    # Materialize the per-issue fanout the gate reduces over.
    lib.write_manifest(seed, PID, instance, ["per-issue"], man)
    for leg, v in zip(man["legs"], verdicts):
        evp = lib.output_artifact_path(
            seed, PID, instance,
            path=lib.state_path(proto, ["per-issue", leg["id"], "honesty-verdict"]))
        os.makedirs(os.path.dirname(evp), exist_ok=True)
        with open(evp, "w") as f:
            json.dump({"conclusion": v["conclusion"], "summary": v["summary"]}, f)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed", id_=True)
    subprocess.run(["git", "-C", seed, "remote", "add", "origin", bare], check=True)
    git(seed, "push", "-q", "-u", "origin", "agentic-state")

    work = os.path.join(tmp, "work")
    env = dict(os.environ, STATE_REMOTE=bare, STATE_BRANCH="agentic-state",
               NODE_PATH="honesty-gate", ENGINE_LOCAL="1")
    proc = subprocess.run([sys.executable, NEXT, work, instance, PROTO, "continue", "deadbeef"],
                          env=env, capture_output=True, text=True)
    chk = os.path.join(tmp, "verify")
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", bare, chk], check=True)
    inst_data = yaml.safe_load(open(os.path.join(chk, PID, instance, "_instance.yaml")))
    return proc, inst_data, chk


# --- Scenario 1: one dishonest leg -> aggregate-honesty blocked -> HALT ---------- #
proc, inst, _chk = drive("pr-dishonest", [
    {"number": 101, "conclusion": "success", "summary": "HONEST"},
    {"number": 102, "conclusion": "failure", "summary": "NOT honest — caught"}])
ok("[dishonest] next.py exits 0", proc.returncode == 0)
ok("[dishonest] halted marker for the honesty-gate phase",
   (inst.get("halted") or {}).get("reason") == "blocked"
   and inst["halted"].get("phase") == "honesty-gate")
ok("[dishonest] did NOT advance to post-fix", inst.get("phase") != "post-fix")
ok("[dishonest] did NOT dispatch a continue past the gate",
   "event_type=protocol-continue" not in proc.stderr)
# The reducer's own blocked-summary ('N/total issue(s) dishonest: 102') propagated
# into the halt comment — proves aggregate-honesty read the per-issue verdicts.
ok("[dishonest] halt surfaces aggregate-honesty's dishonest rollup (issue 102)",
   "dishonest" in proc.stderr and "102" in proc.stderr)
if inst.get("halted") is None:
    print("STDOUT:\n", proc.stdout, "\nSTDERR:\n", proc.stderr)

# --- Scenario 2: all honest -> not blocked -> continue to post-fix --------------- #
proc, inst, chk = drive("pr-honest", [
    {"number": 101, "conclusion": "success", "summary": "HONEST"},
    {"number": 102, "conclusion": "success", "summary": "HONEST"}])
ok("[honest] next.py exits 0", proc.returncode == 0)
ok("[honest] no halted marker", "halted" not in inst)
ok("[honest] advanced the cursor to post-fix", inst.get("phase") == "post-fix")
ok("[honest] dispatched a continue onto post-fix",
   "event_type=protocol-continue" in proc.stderr and "post-fix" in proc.stderr)

# FOLLOWUP #2: the clear-gate `.next` arm must PERSIST the merge rollup as the
# honesty-gate phase's evidence, at exactly the path a downstream `{from:'honesty-gate'}`
# agent input resolves to — otherwise mrp's `{from:'honesty-gate', as:'honesty'}` reads a
# non-existent file. Resolve that path the SAME way the engine does for mrp, then assert
# the file exists on the state branch and carries aggregate-honesty's rollup.
_proto = json.load(open(PROTO))
_resolved = lib.resolve_inputs(
    _proto, chk, PID, "pr-honest", consuming_branch=None, consuming_phase=None,
    inputs=[{"from": "honesty-gate", "as": "honesty"}], consuming_path=["mrp"])
honesty_ev_path = _resolved[0]["path"]
ok("[honest] mrp's {from:honesty-gate} input now resolves to a written file",
   os.path.isfile(honesty_ev_path))
if os.path.isfile(honesty_ev_path):
    rollup = json.load(open(honesty_ev_path))
    ok("[honest] persisted evidence carries the aggregate-honesty rollup keys",
       rollup.get("conclusion") == "success" and rollup.get("blocked") is False
       and isinstance(rollup.get("rollup"), dict)
       and rollup["rollup"].get("total") == 2)
else:
    print("resolved honesty-gate evidence path (missing):", honesty_ev_path)

if failures:
    print("FAIL test_honesty_gate_engine:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - honesty-gate: dishonest set HALTS, clean set continues to post-fix")

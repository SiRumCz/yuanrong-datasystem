#!/usr/bin/env python3
"""Task 11 capstone — per-issue honesty phase, driven END-TO-END through the REAL
`next.py` (a subprocess) against a bare `agentic-state` remote under ENGINE_LOCAL.
Agent steps are stubbed with fixture evidence (no real agents run); the ENGINE and
the REAL code-review protocol/hooks are exercised for real.

Four sub-scenarios (sibling `ok(name, cond)` pattern), each asserting genuine
on-disk state (re-cloned `_instance.yaml`, persisted manifest, dispatched
`event_type=` in stderr, per-leg evidence paths):

  [A] N distinct pinned issues — entering `per-issue` materializes one leg per
      expander item; each leg's `triage` receives its OWN `inputs.issue.number`
      (distinct pins → no fanout collapse) at the delivery seam (next.py:144-148).
  [B] Halt on dishonest — a materialized per-issue fanout with one dishonest leg
      makes `aggregate-honesty` set blocked → the top-merge Option-B arm
      (next.py:862-878) writes the `halted` marker and does NOT continue to post-fix.
  [C] /override resumes — from B's halted remote, `override` clears the marker and
      advances the cursor to `next_sibling(honesty-gate) == post-fix` (do_override,
      next.py:305-355).
  [D] 0-issue clean PR — (D1) entering `per-issue` with 0 items fires the
      empty-fanout short-circuit (next.py:768-778: join dispatched + noop, no dead
      run-fanout); (D2) a vacuous honesty-gate (0-leg manifest) is NOT blocked and
      the top-merge `.next` arm flows on to post-fix — a clean PR advances, not stalls.

Harness modeled on test_engine_iterate_state.py / test_per_issue_fanout.py /
test_honesty_gate_engine.py (bare-git seeding, positional
`next.py <work> <instance> <proto> <cmd> <sha>`, STATE_REMOTE/STATE_BRANCH/
NODE_PATH/ENGINE_LOCAL env).

Two verified facts this test relies on (they diverge from an early brief sketch):
  * The honesty-verdict evidence ABI is conclude-honesty's {conclusion,summary};
    an issue is dishonest iff conclusion != 'success' (aggregate-honesty:12-15).
    (NOT a {honest:bool} self-claim — that is ignored by the reducer.)
  * The ENGINE_LOCAL `expand-issues` stub is pinned to items.json beside it and the
    run_expander env allowlist drops EXPAND_ISSUES_FILE, so to drive A (3 distinct
    issues) and D1 (0 issues) with the REAL per-issue node we point next.py at a
    tmp COPY of the real protocol dir whose expand/items.json we control — no
    checked-in fixture is mutated. B/C/D2 use the real protocol directly (they seed
    the manifest, never the expander).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import yaml  # PyYAML is a declared runtime dep of the engine

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "engine"))
NEXT = os.path.join(ENGINE, "next.py")
PROTO_DIR = os.path.normpath(os.path.join(HERE, ".."))          # the REAL code-review dir
PROTO = os.path.join(PROTO_DIR, "protocol.json")               # the REAL protocol
GIT_ID = ["-c", "user.email=test@engine", "-c", "user.name=engine-test"]
PID = "code-review"

sys.path.insert(0, ENGINE)
import lib  # noqa: E402  (seed manifests/evidence exactly where the engine reads them)

failures = []
_clone_n = 0


def ok(name, cond):
    if not cond:
        failures.append(name)


def git(cwd, *a, id_=False):
    subprocess.run(["git", "-C", cwd] + (GIT_ID if id_ else []) + list(a),
                   check=True, capture_output=True, text=True)


def run_next(tmp, bare, instance, proto, command, node_path=None, extra_env=None, tag="work"):
    """Clone `bare` into a fresh work dir and drive the REAL next.py. Returns
    (work, proc). `work` must not pre-exist — next.py's state_checkout clones into it."""
    work = os.path.join(tmp, f"{instance}-{tag}")
    env = dict(os.environ, STATE_REMOTE=bare, STATE_BRANCH="agentic-state", ENGINE_LOCAL="1")
    if node_path is not None:
        env["NODE_PATH"] = node_path
    else:
        env.pop("NODE_PATH", None)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run([sys.executable, NEXT, work, instance, proto, command, "deadbeef"],
                          env=env, capture_output=True, text=True)
    return work, proc


def parse_action(stdout, want=None):
    """Last JSON action line on stdout (optionally filtered by action name)."""
    act = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        if want is None or j.get("action") == want:
            act = j
    return act


def reclone(tmp, bare, instance):
    global _clone_n
    _clone_n += 1
    chk = os.path.join(tmp, f"verify-{instance}-{_clone_n}")
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", bare, chk], check=True)
    return chk


def read_instance(tmp, bare, instance):
    chk = reclone(tmp, bare, instance)
    return yaml.safe_load(open(os.path.join(chk, PID, instance, "_instance.yaml")))


def mk_proto_copy(tmp, items):
    """Tmp copy of the REAL protocol whose expander yields exactly `items` under
    ENGINE_LOCAL. Only protocol.json + expand/ are needed for the per-issue ENTRY
    scenarios (they never reach the honesty-gate merge); validate_protocol is
    fs-free, so no sibling schema files are required."""
    dst = tempfile.mkdtemp(dir=tmp, prefix="proto-")
    shutil.copy2(PROTO, os.path.join(dst, "protocol.json"))
    shutil.copytree(os.path.join(PROTO_DIR, "expand"), os.path.join(dst, "expand"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    with open(os.path.join(dst, "expand", "items.json"), "w") as f:
        json.dump(items, f)
    return os.path.join(dst, "protocol.json")


def mk_perissue_remote(tmp, instance):
    """Bare agentic-state remote seeded at phase `per-issue` (pre-fanout)."""
    bare = os.path.join(tmp, f"{instance}.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    seed = os.path.join(tmp, f"{instance}-seed")
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
    return bare


def mk_gate_remote(tmp, instance, verdicts):
    """Bare remote at phase `honesty-gate` with the per-issue fanout ALREADY
    materialized: a manifest (one leg per verdict, production leg-id shape) and each
    leg's terminal honesty-verdict evidence at the exact path collect_fanout_evidence
    reads (per-issue.<leg>.honesty-verdict.evidence.json). `verdicts` is a list of
    (issue_number:int, honest:bool); [] → a 0-leg (vacuous) manifest, no evidence.
    honest→conclusion 'success', dishonest→'failure' (aggregate-honesty's ABI)."""
    proto = json.load(open(PROTO))
    man = lib.build_manifest([{"number": n} for n, _ in verdicts], "$.number", 32)
    bare = os.path.join(tmp, f"{instance}.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    seed = os.path.join(tmp, f"{instance}-seed")
    subprocess.run(["git", "init", "-q", seed], check=True)
    subprocess.run(["git", "-C", seed, "symbolic-ref", "HEAD", "refs/heads/agentic-state"], check=True)
    inst = os.path.join(seed, PID, instance)
    os.makedirs(inst)
    with open(os.path.join(inst, "_instance.yaml"), "w") as f:
        f.write(f"protocol: {PID}\ninstance: {instance}\nhead_sha: deadbeef\nphase: honesty-gate\njoined: false\n")
    lib.write_manifest(seed, PID, instance, ["per-issue"], man)
    for (n, honest), leg in zip(verdicts, man["legs"]):
        evp = lib.output_artifact_path(
            seed, PID, instance,
            path=lib.state_path(proto, ["per-issue", leg["id"], "honesty-verdict"]))
        os.makedirs(os.path.dirname(evp), exist_ok=True)
        with open(evp, "w") as f:
            json.dump({"conclusion": "success" if honest else "failure",
                       "summary": (f"issue {n} HONEST" if honest
                                   else f"issue {n} NOT honest — caught")}, f)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed", id_=True)
    subprocess.run(["git", "-C", seed, "remote", "add", "origin", bare], check=True)
    git(seed, "push", "-q", "-u", "origin", "agentic-state")
    return bare


with tempfile.TemporaryDirectory() as TMP:
    # ── [A] N distinct pinned issues ──────────────────────────────────────── #
    proto_a = mk_proto_copy(TMP, [{"number": 11, "title": "[ai-review] a"},
                                  {"number": 22, "title": "[ai-review] b"},
                                  {"number": 33, "title": "[ai-review] c"}])
    bare = mk_perissue_remote(TMP, "pr-pins")
    _, r = run_next(TMP, bare, "pr-pins", proto_a, "continue", node_path="per-issue")
    ok("[A] next.py exits 0", r.returncode == 0)
    act = parse_action(r.stdout, want="run-fanout")
    ok("[A] emits a run-fanout", act is not None)
    if act is None:
        print("STDOUT:\n", r.stdout, "\nSTDERR:\n", r.stderr)
    else:
        legs = act.get("legs", [])
        ids = [leg["id"] for leg in act.get("branches", [])]
        ok("[A] 3 legs materialized", len(legs) == 3 and len(ids) == 3)
        ok("[A] leg ids == sha1(number)[:8]",
           ids == [lib.leg_id(11), lib.leg_id(22), lib.leg_id(33)])
        ok("[A] each leg pins its OWN issue number (no collapse)",
           [leg["inputs"]["issue"]["number"] for leg in legs] == [11, 22, 33])
        ok("[A] every leg enters its own `triage` subtree (distinct)",
           len({leg["path"] for leg in legs}) == 3
           and all(leg["path"].split(".")[-1] == "triage" for leg in legs))
        ok("[A] each leg runs fix-triage-agent",
           all(leg.get("workflow") == "fix-triage-agent" for leg in legs))
    # the 3-leg manifest was materialized + pushed
    man_a = os.path.join(reclone(TMP, bare, "pr-pins"), PID, "pr-pins", "per-issue.__manifest.yaml")
    ok("[A] 3-leg manifest persisted on the state branch",
       os.path.isfile(man_a) and (yaml.safe_load(open(man_a)) or {}).get("count") == 3)

    # ── [B] injected dishonest leg halts honesty-gate ─────────────────────── #
    bare_h = mk_gate_remote(TMP, "pr-halt", [(11, True), (22, False), (33, True)])
    _, rb = run_next(TMP, bare_h, "pr-halt", PROTO, "continue",
                     node_path="honesty-gate", tag="gate")
    ok("[B] next.py exits 0", rb.returncode == 0)
    inst = read_instance(TMP, bare_h, "pr-halt")
    ok("[B] halted marker written (reason=blocked)",
       (inst.get("halted") or {}).get("reason") == "blocked")
    ok("[B] halted phase is honesty-gate",
       (inst.get("halted") or {}).get("phase") == "honesty-gate")
    ok("[B] cursor did NOT advance to post-fix", inst.get("phase") != "post-fix")
    ok("[B] not finalized (joined stays false)", inst.get("joined") in (False, None))
    ok("[B] did NOT dispatch a continue past the gate",
       "event_type=protocol-continue" not in rb.stderr)
    ok("[B] halt surfaces aggregate-honesty's dishonest rollup (issue 22)",
       "dishonest" in rb.stderr and "22" in rb.stderr)
    if inst.get("halted") is None:
        print("STDOUT:\n", rb.stdout, "\nSTDERR:\n", rb.stderr)

    # ── [C] /override resumes to post-fix (same halted remote) ────────────── #
    _, rc = run_next(TMP, bare_h, "pr-halt", PROTO, "override", node_path=None,
                     extra_env={"OVERRIDE_ACTOR": "tester",
                                "OVERRIDE_REASON": "manual review — cleared"},
                     tag="override")
    ok("[C] next.py exits 0", rc.returncode == 0)
    ok("[C] override dispatched a continue to post-fix",
       "override:continue:post-fix" in rc.stdout)
    ok("[C] override fired the protocol-continue onto post-fix",
       "event_type=protocol-continue" in rc.stderr and "post-fix" in rc.stderr)
    insc = read_instance(TMP, bare_h, "pr-halt")
    ok("[C] halted marker cleared", "halted" not in insc)
    ok("[C] cursor advanced to post-fix", insc.get("phase") == "post-fix")
    ok("[C] override recorded against honesty-gate",
       (insc.get("overrides") or [{}])[0].get("phase") == "honesty-gate")
    ok("[C] override records the actor",
       (insc.get("overrides") or [{}])[0].get("actor") == "tester")

    # ── [D1] 0-issue PR: empty-fanout short-circuit on entering per-issue ─── #
    proto_d = mk_proto_copy(TMP, [])                    # expander yields zero items
    bare_e = mk_perissue_remote(TMP, "pr-empty")
    _, rd = run_next(TMP, bare_e, "pr-empty", proto_d, "continue", node_path="per-issue")
    ok("[D1] next.py exits 0", rd.returncode == 0)
    actd = parse_action(rd.stdout)
    ok("[D1] emits a noop, not a dead run-fanout",
       actd is not None and actd.get("action") == "noop"
       and actd.get("reason") == "empty-fanout:per-issue")
    ok("[D1] never emits run-fanout with empty legs", '"run-fanout"' not in rd.stdout)
    ok("[D1] fires the fanout's protocol-join (advances, not stalls)",
       "event_type=protocol-join" in rd.stderr)
    man_e = os.path.join(reclone(TMP, bare_e, "pr-empty"), PID, "pr-empty", "per-issue.__manifest.yaml")
    ok("[D1] 0-leg manifest persisted",
       os.path.isfile(man_e) and (yaml.safe_load(open(man_e)) or {}).get("count") == 0)
    if actd is None or actd.get("reason") != "empty-fanout:per-issue":
        print("STDOUT:\n", rd.stdout, "\nSTDERR:\n", rd.stderr)

    # ── [D2] vacuous honesty-gate continues to post-fix (clean PR → done) ──── #
    bare_v = mk_gate_remote(TMP, "pr-vac", [])          # 0-leg manifest at the gate
    _, rv = run_next(TMP, bare_v, "pr-vac", PROTO, "continue",
                     node_path="honesty-gate", tag="gate")
    ok("[D2] next.py exits 0", rv.returncode == 0)
    insv = read_instance(TMP, bare_v, "pr-vac")
    ok("[D2] vacuous gate is NOT halted", "halted" not in insv)
    ok("[D2] cursor flows past the gate to post-fix", insv.get("phase") == "post-fix")
    ok("[D2] dispatched a continue onto post-fix",
       "event_type=protocol-continue" in rv.stderr and "post-fix" in rv.stderr)

if failures:
    print("FAIL test_per_issue_e2e:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - per-issue e2e: N distinct pins; dishonest leg halts; override→post-fix; 0-issue→post-fix")

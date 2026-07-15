#!/usr/bin/env python3
"""WS5 / CRIT-1 regression guard: a NESTED per-issue sub-pipeline state's `conclude`
hook must FIRE when the leg advances.

Before the fix, advance.py's sub-pipeline done-arm (`if branch and substate:`) only
advanced the branch cursor — it NEVER ran the leg node's `conclude` hook. The root
child block that DOES run conclude is reached only by depth-1 phases, so a nested
`conclude` (per-issue triage's `conclude-triage`, fix's `conclude-fix`) silently
never fired: the whole per-issue commit/push/close leg was dead. This drives the
REAL advance.py against a bare `agentic-state` remote (ENGINE_LOCAL) over a fixture
whose per-issue `each` has triage/fix sub-states with a STUB `conclude` that writes
a marker (NO real conclude-fix/git). RED before the fix (no marker), GREEN after.

  [triage] advancing per-issue.<leg>.triage (a nested sub-state with a conclude)
           runs the stub conclude → marker written; cursor advances to `fix`.
  [fix]    advancing per-issue.<leg>.fix runs its conclude AND the hook receives its
           declared `triage` input (CONCLUDE_INPUTS_DIR/triage.json) — exercising the
           path-aware nested input resolution the fix also required.

Harness modeled on test_engine_iterate_state.py (bare-git seeding, positional
advance.py ABI, STATE_REMOTE/STATE_BRANCH/NODE_PATH/ENGINE_LOCAL env)."""
import json
import os
import subprocess
import sys
import tempfile

import yaml  # PyYAML is a declared runtime dep of the engine

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "engine"))
ADVANCE = os.path.join(ENGINE, "advance.py")
PROTO = os.path.join(HERE, "fixtures", "nested-conclude", "protocol.json")
GIT_ID = ["-c", "user.email=test@engine", "-c", "user.name=engine-test"]
LEG = "issue-1"  # any leg id resolves against the `each` template (expand fanout)

sys.path.insert(0, ENGINE)
import lib  # noqa: E402  (seed state/evidence exactly where the engine reads them)

PID = json.load(open(PROTO))["name"]
PROTO_DATA = json.load(open(PROTO))
failures = []


def ok(name, cond):
    if not cond:
        failures.append(name)


def git(cwd, *a, id_=False):
    subprocess.run(["git", "-C", cwd] + (GIT_ID if id_ else []) + list(a),
                   check=True, capture_output=True, text=True)


def leg_state_file(root, tree_path):
    return lib.state_file(root, PID, "pr-1", path=lib.state_path(PROTO_DATA, tree_path))


def setup_remote(tmp, instance, seed_states, seed_triage_evidence=False):
    """Bare agentic-state remote at phase `per-issue` with the given leg sub-state
    files materialized. `seed_states` maps a tree_path tuple -> state dict."""
    bare = os.path.join(tmp, f"{instance}.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    seed = os.path.join(tmp, f"{instance}-seed")
    subprocess.run(["git", "init", "-q", seed], check=True)
    subprocess.run(["git", "-C", seed, "symbolic-ref", "HEAD", "refs/heads/agentic-state"], check=True)
    inst = os.path.join(seed, PID, instance)
    os.makedirs(inst)
    with open(os.path.join(inst, "_instance.yaml"), "w") as f:
        f.write(f"protocol: {PID}\ninstance: {instance}\n"
                "head_sha: deadbeef\nphase: per-issue\njoined: false\n")
    for tree_path, st in seed_states.items():
        sf = lib.state_file(seed, PID, instance, path=lib.state_path(PROTO_DATA, list(tree_path)))
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        with open(sf, "w") as f:
            yaml.safe_dump(st, f, sort_keys=False)
    if seed_triage_evidence:
        evp = lib.output_artifact_path(
            seed, PID, instance,
            path=lib.state_path(PROTO_DATA, ["per-issue", LEG, "triage"]), kind="evidence")
        os.makedirs(os.path.dirname(evp), exist_ok=True)
        with open(evp, "w") as f:
            json.dump({"clusters": [], "summary": {}}, f)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed", id_=True)
    subprocess.run(["git", "-C", seed, "remote", "add", "origin", bare], check=True)
    git(seed, "push", "-q", "-u", "origin", "agentic-state")
    return bare


def run_advance(tmp, bare, instance, node_path, marker):
    """Drive the REAL advance.py done-arm for the leg at `node_path` (all checks
    pass). Returns (proc, marker_dict_or_None)."""
    work = os.path.join(tmp, f"{instance}-work")  # must NOT exist; cloned into
    vpath = os.path.join(tmp, f"{instance}-verdicts.json")
    with open(vpath, "w") as f:
        json.dump({"results": [{"check": "evidence-present", "pass": True, "on_fail": "iterate"}]}, f)
    evid = os.path.join(tmp, f"{instance}-evidence.json")
    with open(evid, "w") as f:
        json.dump({"clusters": [], "summary": {}}, f)
    env = dict(os.environ, STATE_REMOTE=bare, STATE_BRANCH="agentic-state",
               NODE_PATH=node_path, ENGINE_LOCAL="1", MARKER_FILE=marker)
    proc = subprocess.run([sys.executable, ADVANCE, work, instance, PROTO, vpath, evid],
                          env=env, capture_output=True, text=True)
    m = json.load(open(marker)) if os.path.isfile(marker) else None
    return proc, m


LEG_STATE = {"protocol": PID, "instance": "pr-1", "state": "per-issue",
             "iteration": 1, "gates": {}, "head_sha": "deadbeef", "history": []}

with tempfile.TemporaryDirectory() as tmp:
    # ── [triage] nested conclude fires; cursor advances to fix ──────────────── #
    bare = setup_remote(tmp, "pr-1", {("per-issue", LEG, "triage"): dict(LEG_STATE)})
    marker = os.path.join(tmp, "triage.marker")
    proc, m = run_advance(tmp, bare, "pr-1", f"per-issue.{LEG}.triage", marker)
    ok("[triage] advance.py exits 0", proc.returncode == 0)
    ok("[triage] nested conclude-triage FIRED (marker written)", m is not None and m.get("ran") is True)
    if m is None:
        print("TRIAGE STDERR:\n", proc.stderr)
    # cursor advanced to the next sub-state `fix`
    chk = os.path.join(tmp, "verify-triage")
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", bare, chk], check=True)
    cursor_sf = os.path.join(chk, PID, "pr-1",
                             ".".join(lib.state_path(PROTO_DATA, ["per-issue", LEG])) + ".yaml")
    cur = yaml.safe_load(open(cursor_sf)) if os.path.isfile(cursor_sf) else {}
    ok("[triage] leg cursor advanced to fix", cur.get("sub_state") == "fix")

    # ── [fix] nested conclude fires AND receives its declared triage input ──── #
    bare2 = setup_remote(tmp, "pr-2", {("per-issue", LEG, "fix"): dict(LEG_STATE)},
                         seed_triage_evidence=True)
    marker2 = os.path.join(tmp, "fix.marker")
    proc2, m2 = run_advance(tmp, bare2, "pr-2", f"per-issue.{LEG}.fix", marker2)
    ok("[fix] advance.py exits 0", proc2.returncode == 0)
    ok("[fix] nested conclude-fix FIRED (marker written)", m2 is not None and m2.get("ran") is True)
    ok("[fix] conclude-fix received its declared `triage` input",
       m2 is not None and m2.get("triage_seen") is True)
    if m2 is None:
        print("FIX STDERR:\n", proc2.stderr)

if failures:
    print("FAIL test_engine_nested_conclude:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - nested per-issue conclude hooks fire (triage + fix, with declared inputs)")

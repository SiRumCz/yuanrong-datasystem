#!/usr/bin/env python3
"""Task 8 — the per-issue dynamic fanout materializes ONE leg per open [ai-review]
issue, each entering `triage` (fix-triage-agent) with its delivered issue threaded
in as `.inputs.issue`.

Two checks:
  Part A (unit)   — lib.build_manifest over the ENGINE_LOCAL expand-issues items
                    yields 2 distinct <sha1(number)[:8]> legs, and matrix_fields
                    projection inlines only `number` as each leg's issue input.
  Part B (engine) — drive the REAL next.py against the REAL code-review protocol on
                    a bare agentic-state remote: entering `per-issue` emits a
                    run-fanout with 2 legs whose leaf path ends in `.triage`,
                    workflow `fix-triage-agent`, inputs `{issue:{number:N}}`.
"""
import hashlib, json, os, subprocess, sys, tempfile
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "..", "..", "engine")
NEXT = os.path.join(ENGINE, "next.py")
PROTO = os.path.join(HERE, "..", "protocol.json")            # the REAL code-review protocol
EXPANDER = os.path.join(HERE, "..", "expand", "expand-issues")
GIT_ID = ["-c", "user.email=test@engine", "-c", "user.name=engine-test"]
PID = "code-review"
failures = []

def ok(name, cond):
    if not cond:
        failures.append(name)

def git(cwd, *a, id_=False):
    subprocess.run(["git", "-C", cwd] + (GIT_ID if id_ else []) + list(a),
                   check=True, capture_output=True, text=True)

# --- Part A: build_manifest unit check -------------------------------------- #
sys.path.insert(0, ENGINE)
import lib

r = subprocess.run([EXPANDER], capture_output=True, text=True,
                   env={"ENGINE_LOCAL": "1", "PATH": "/usr/bin:/bin"})
items = json.loads(r.stdout)["items"]
man = lib.build_manifest(items, "$.number", 32)        # (items, id_from, max_legs<int>)
legs = man["legs"]
ok("[A] 2 legs", len(legs) == 2)
ok("[A] leg0 id == sha1('101')[:8]",
   legs and legs[0]["id"] == hashlib.sha1(b"101").hexdigest()[:8])
ok("[A] distinct leg ids", len(legs) == 2 and legs[0]["id"] != legs[1]["id"])
ok("[A] keys are the issue numbers", [l["key"] for l in legs] == [101, 102])
ok("[A] matrix projection inlines only number",
   [lib.project_matrix_item(l["item"], ["number"]) for l in legs]
   == [{"number": 101}, {"number": 102}])

# --- Part B: drive the real next.py entering the per-issue fanout ------------ #
with tempfile.TemporaryDirectory() as tmp:
    instance = "pr-perissue"
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

    work = os.path.join(tmp, "work")
    env = dict(os.environ, STATE_REMOTE=bare, STATE_BRANCH="agentic-state",
               NODE_PATH="per-issue", ENGINE_LOCAL="1")
    proc = subprocess.run([sys.executable, NEXT, work, instance, PROTO, "continue", "deadbeef"],
                          env=env, capture_output=True, text=True)
    ok("[B] next.py exits 0", proc.returncode == 0)

    act = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                cand = json.loads(line)
            except Exception:
                continue
            if cand.get("action") == "run-fanout":
                act = cand
    ok("[B] emits a run-fanout", act is not None)
    if act is None:
        print("STDOUT:\n", proc.stdout, "\nSTDERR:\n", proc.stderr)
    else:
        elegs = act.get("legs", [])
        ok("[B] 2 per-issue legs", len(elegs) == 2)
        ok("[B] every leg enters `triage`",
           all(l["path"].split(".")[-1] == "triage" for l in elegs))
        ok("[B] every leg runs fix-triage-agent",
           all(l.get("workflow") == "fix-triage-agent" for l in elegs))
        ok("[B] each leg carries its delivered issue as .inputs.issue",
           sorted(l["inputs"]["issue"]["number"] for l in elegs) == [101, 102])
        ok("[B] leg leaf paths are distinct per-issue subtrees",
           len({l["path"] for l in elegs}) == 2)

    check = os.path.join(tmp, "verify")
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", bare, check], check=True)
    manp = os.path.join(check, PID, instance, "per-issue.__manifest.yaml")
    ok("[B] 2-leg manifest persisted",
       os.path.isfile(manp) and (yaml.safe_load(open(manp)) or {}).get("count") == 2)

if failures:
    print("FAIL test_per_issue_fanout:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - per-issue fanout materializes 2 legs, each entering triage")

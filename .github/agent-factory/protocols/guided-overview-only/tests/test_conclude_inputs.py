#!/usr/bin/env python3
"""Engine conclude hooks receive materialized declared inputs.

This imports the vendored engine helper directly and verifies:
  - a state with inputs gets CONCLUDE_INPUTS_DIR containing <as>.json;
  - a state without inputs does not get CONCLUDE_INPUTS_DIR.
"""
import importlib.util
import json
import os
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", "..", "engine"))
ADVANCE = os.path.join(ENGINE, "advance.py")

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}: got {got!r} want {want!r}")


spec = importlib.util.spec_from_file_location("advance", ADVANCE)
advance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advance)
lib = advance.lib

root = tempfile.mkdtemp()
proto_dir = os.path.join(root, "proto")
publish_dir = os.path.join(proto_dir, "publish")
os.makedirs(publish_dir, exist_ok=True)

stub = os.path.join(publish_dir, "stub-conclude.py")
with open(stub, "w") as fh:
    fh.write(
        """#!/usr/bin/env python3
import json, os
d = os.environ.get("CONCLUDE_INPUTS_DIR", "")
seen = os.path.isfile(os.path.join(d, "triage.json")) if d else False
print(json.dumps({"conclusion": "neutral",
                  "summary": f"inputs_dir={d} triage_seen={seen}",
                  "blocked": False}))
"""
    )
os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)

proto = {
    "name": "code-review",
    "states": [
        {
            "id": "review",
            "kind": "fanout",
            "branches": [{"id": "correctness", "workflow": "noop"}],
        },
        {"id": "triage", "kind": "agent", "workflow": "noop"},
        {
            "id": "fix",
            "kind": "agent",
            "workflow": "noop",
            "inputs": [{"from": "triage", "as": "triage"}],
            "conclude": "stub-conclude",
        },
        {
            "id": "overview",
            "kind": "agent",
            "workflow": "noop",
            "conclude": "stub-conclude",
        },
    ],
}
proto_path = os.path.join(proto_dir, "protocol.json")
with open(proto_path, "w") as fh:
    json.dump(proto, fh)

state_dir = os.path.join(root, "state")
instance = "pr-1"
triage_path = lib.output_artifact_path(
    state_dir,
    "code-review",
    instance,
    path=lib.state_path(proto, ["triage"]),
    kind="evidence",
)
os.makedirs(os.path.dirname(triage_path), exist_ok=True)
with open(triage_path, "w") as fh:
    json.dump({"clusters": [], "summary": {}}, fh)

evidence_path = os.path.join(root, "evidence.json")
with open(evidence_path, "w") as fh:
    json.dump({}, fh)

old_env = os.environ.pop("CONCLUDE_INPUTS_DIR", None)
try:
    with_inputs = advance.run_conclude_hook(
        proto_path,
        proto,
        "fix",
        evidence_path,
        instance,
        blocking=False,
        dir_=state_dir,
        tree_path=["fix"],
    )
    without_inputs = advance.run_conclude_hook(
        proto_path,
        proto,
        "overview",
        evidence_path,
        instance,
        blocking=False,
        dir_=state_dir,
        tree_path=["overview"],
    )
finally:
    if old_env is not None:
        os.environ["CONCLUDE_INPUTS_DIR"] = old_env

check("with_inputs conclusion", with_inputs["conclusion"], "neutral")
if "triage_seen=True" not in with_inputs.get("summary", ""):
    failures.append(f"fix conclude hook did not see triage input: {with_inputs!r}")
if "inputs_dir=" not in with_inputs.get("summary", ""):
    failures.append(f"fix conclude hook did not receive inputs dir: {with_inputs!r}")
if "inputs_dir= triage_seen=False" not in without_inputs.get("summary", ""):
    failures.append(f"input-less hook unexpectedly received inputs dir: {without_inputs!r}")

if failures:
    print("FAIL test_conclude_inputs:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - conclude hooks receive materialized inputs only when declared")

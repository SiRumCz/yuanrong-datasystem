#!/usr/bin/env python3
"""Engine shared library. Importable by the engine scripts AND a thin CLI
(`python3 lib.py <subcommand> ...`) for helpers the orchestrator calls inline."""
import glob
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import yaml
import paths as _paths

STATE_REMOTE = os.environ.get("STATE_REMOTE", "")
STATE_BRANCH = os.environ.get("STATE_BRANCH", "agentic-state")
GIT_ID = ["-c", "user.email=engine@agentic-protocol-poc",
          "-c", "user.name=protocol-engine"]


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def dump_yaml(path, data):
    # sort_keys=False + block style keeps a stable, human-readable git trail.
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def git(dir_, *args, check=True, capture=False):
    return subprocess.run(["git", "-C", dir_] + list(args),
                          check=check, text=True, capture_output=capture)


def protocol_id(proto_path):
    """protocol_id <protocol.json> — the protocol's id."""
    with open(proto_path) as f:
        return json.load(f)["name"]


def _coord_to_path(branch=None, phase=None, substate=None):
    """Back-compat: collapse the legacy 3 kwargs to a node-path list."""
    p = []
    if phase:
        p.append(phase)
    if branch:
        p.append(branch)
    if substate:
        p.append(substate)
    return p


def state_file(d, pid, instance, branch=None, phase=None, substate=None, path=None):
    """<dir>/<pid>/<instance>/<dot-joined-path>.yaml (or <instance>.yaml for the
    empty path). `path` is the canonical node-path; the branch/phase/substate
    kwargs are a back-compat shim that builds the equivalent 3-element path.
    Depth-<=3 paths are byte-identical to the historical layout."""
    base = f"{d}/{pid}/{instance}"
    p = list(path) if path is not None else _coord_to_path(branch, phase, substate)
    if not p:
        return f"{base}.yaml"
    return f"{base}/{'.'.join(p)}.yaml"


def state_path(proto, tree_path):
    """Tree-navigation path -> file-naming path. Drop the leading top-level
    fanout/phase id when single-phase (it is omitted from historical filenames);
    keep the full path when multi-phase. The recursive walker passes its tree
    path through this before every state_file/output_artifact_path/join_marker_file
    call, so depth-<=3 files stay byte-identical to the legacy layout."""
    if not tree_path:
        return []
    return list(tree_path) if is_multiphase(proto) else list(tree_path[1:])


def output_artifact_path(d, pid, instance, branch=None, phase=None, substate=None,
                         kind="evidence", path=None):
    """Persisted-output path for a state, parallel to state_file but with a
    .<kind>.json suffix. kind is 'evidence' (agent) or 'answers' (gate)."""
    sf = state_file(d, pid, instance, branch=branch, phase=phase, substate=substate, path=path)
    return sf[:-len(".yaml")] + f".{kind}.json"


def join_marker_file(d, pid, instance, fanout_path):
    """Path to the path-keyed join marker for a nested fanout.
    `fanout_path` is the FILE-NAMING path (already converted via state_path);
    callers in Task 12 pass lib.state_path(proto, tree_path).
    Only nested fanouts (len(tree_path) > 1) should call this — top-level
    fanout join tracking stays on _instance.yaml (back-compat)."""
    base = f"{d}/{pid}/{instance}"
    return f"{base}/{'.'.join(fanout_path)}.__join.yaml"


def read_join(d, pid, instance, fanout_path):
    """Read the path-keyed join marker dict, or {} if it does not exist yet."""
    f = join_marker_file(d, pid, instance, fanout_path)
    return load_yaml(f) if os.path.isfile(f) else {}


def write_join(d, pid, instance, fanout_path, data):
    """Write (overwrite) the path-keyed join marker dict."""
    f = join_marker_file(d, pid, instance, fanout_path)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    dump_yaml(f, data)


def manifest_file(d, pid, instance, tree_path):
    """Path to a dynamic fanout's manifest. Unlike leg/join files this is a NEW
    file with no legacy byte-identity constraint, so it keys by the FULL tree
    path (never dropped by state_path) — always unique and non-empty, for the
    top fanout (['review'] -> review.__manifest.yaml) and nested alike."""
    base = f"{d}/{pid}/{instance}"
    return f"{base}/{'.'.join(tree_path)}.__manifest.yaml"


def read_manifest(d, pid, instance, tree_path):
    """Read the manifest dict, or {} if it does not exist yet."""
    f = manifest_file(d, pid, instance, tree_path)
    return load_yaml(f) if os.path.isfile(f) else {}


def write_manifest(d, pid, instance, tree_path, data):
    f = manifest_file(d, pid, instance, tree_path)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    dump_yaml(f, data)


def resolve_leg_ids(dir_, pid, instance, tree_path, fanout_node):
    """The leg-id list for a fanout: the persisted manifest's ids when dynamic
    (expand present), else the static branches[] ids. The single seam that lets
    join.py treat dynamic and static fanouts uniformly."""
    if fanout_node and fanout_node.get("expand"):
        man = read_manifest(dir_, pid, instance, tree_path)
        return [leg["id"] for leg in man.get("legs", [])]
    return [b["id"] for b in (fanout_node.get("branches", []) if fanout_node else [])]


def collect_fanout_evidence(dir_, pid, instance, tree_path, fanout_node, proto=None):
    """Assemble the reduce input for a `merge` with from_fanout: one row per leg
    in the manifest, carrying its terminal state + persisted evidence (or None).
    Reads from the state branch, never job outputs — resilient to matrix clobber.

    `tree_path` is the fanout's TREE path (e.g. ['review'] for the top fanout, or
    ['review', '<fileleg>', 'findings'] for a nested findings fanout). When `proto`
    is given, each leg is resolved by its FULL tree path (tree_path + [lid]) via
    state_path — nested-aware. If the fanout's `each` is itself a sub-pipeline
    (has `states`), the leg's real OUTPUT evidence lives one level deeper, at its
    terminal sub-state (tree_path + [lid, <last each.states id>]) — the leg
    cursor file at tree_path + [lid] is just the sequence cursor and carries no
    evidence. A flat leg fanout (`each` has no `states`) or a static fanout
    (`branches:`, no `each`) is unaffected. When `proto` is None (back-compat),
    legs are resolved FLAT (branch=leg-id, no path prefix), matching the
    historical single-phase file layout used before nested from_fanout support."""
    man = read_manifest(dir_, pid, instance, tree_path)
    each = (fanout_node or {}).get("each", {})
    out_sub = each["states"][-1]["id"] if isinstance(each, dict) and each.get("states") else None
    rows = []
    for leg in man.get("legs", []):
        lid = leg["id"]
        if proto is not None:
            leg_fp = state_path(proto, list(tree_path) + [lid])
            sf = state_file(dir_, pid, instance, path=leg_fp)          # leg SEQUENCE CURSOR
            evid_tree = list(tree_path) + [lid] + ([out_sub] if out_sub else [])
            evid_fp = state_path(proto, evid_tree)
            evid_path = output_artifact_path(dir_, pid, instance, path=evid_fp)
        else:
            sf = state_file(dir_, pid, instance, lid)          # single-phase leg file
            evid_path = output_artifact_path(dir_, pid, instance, branch=lid, kind="evidence")
        state = ""
        if os.path.isfile(sf):
            try:
                state = load_yaml(sf).get("state", "") or ""
            except Exception:
                state = ""
        evidence = None
        if os.path.isfile(evid_path):
            try:
                with open(evid_path) as f:
                    evidence = json.load(f)
            except (json.JSONDecodeError, ValueError):
                evidence = None
        rows.append({"leg_id": lid, "key": leg.get("key"), "state": state, "evidence": evidence})
    return rows


def leg_id(raw_key):
    """Stable, filesystem-safe leg id from an item's raw id_from value.
    A short sha1 hex is alnum by construction (no sanitizing needed)."""
    return hashlib.sha1(str(raw_key).encode("utf-8")).hexdigest()[:8]


def extract_key(item, id_from):
    """Resolve a simple JSONPath (`$.a.b`) against an item dict. Only the
    dotted-`$.`-rooted form is supported (YAGNI — no wildcards/filters)."""
    if not id_from.startswith("$."):
        raise ValueError(f"id_from must start with '$.', got {id_from!r}")
    cur = item
    for seg in id_from[2:].split("."):
        if not isinstance(cur, dict) or seg not in cur:
            raise ValueError(f"id_from {id_from!r} did not resolve on item {item!r}")
        cur = cur[seg]
    return cur


def build_manifest(items, id_from, max_legs):
    """Turn the expander's items list into a manifest dict. Fails loud on
    over-cap (> max_legs) and on duplicate leg keys."""
    if len(items) > max_legs:
        raise ValueError(f"expander emitted {len(items)} items > max_legs {max_legs}")
    legs, seen = [], {}
    for item in items:
        key = extract_key(item, id_from)
        lid = leg_id(key)
        if lid in seen:
            raise ValueError(f"two items map to leg id '{lid}' (keys {seen[lid]!r} and {key!r})")
        seen[lid] = key
        legs.append({"id": lid, "key": key, "item": item})
    return {"count": len(legs), "legs": legs}


def run_expander(dir_, pid, instance, proto_path, fanout_node):
    """Run a dynamic fanout's trusted expander hook and return its items list.
    Resolved from <protocol-dir>/expand/<hook>. Raises ValueError (fail loud) on
    unresolved / non-executable / nonzero / non-JSON / missing-`items` output.

    Runs in zone 1 (plan); the hook re-fetches the diff itself and is handed only
    a read token via a strict env allowlist (ENFORCED, not aspirational — the
    plan job's full env, including STATE_REMOTE / PUBLISH_TOKEN / the broad
    dispatch PAT, is never forwarded). Under ENGINE_LOCAL the stub reads a
    fixture file instead."""
    pdir = os.path.dirname(os.path.abspath(proto_path))
    expand = fanout_node.get("expand", {})
    res = resolve_executable(f"{pdir}/expand", expand.get("hook", ""), pdir, expand.get("exec", ""))
    kind, path = res.split("\t", 1)
    if kind == "ERR" or not os.access(path, os.X_OK):
        raise ValueError(f"expander '{expand.get('hook')}' unresolved/not-exec: {path}")
    # SECURITY (spec §5): scope the expander to a read-only token. Build the env
    # from a strict ALLOWLIST — never the plan job's full env — so STATE_REMOTE /
    # PUBLISH_TOKEN / the broad dispatch PAT are dropped by default (a future added
    # plan-job env var cannot leak). The expander gets only a read token to fetch
    # the diff.
    _ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "PR", "ENGINE_LOCAL", "GITHUB_REPOSITORY")
    env = {k: os.environ[k] for k in _ALLOW if k in os.environ}
    env.setdefault("PR", instance[len("pr-"):] if instance.startswith("pr-") else instance)
    tok = os.environ.get("EXPANDER_TOKEN")
    if tok:
        env["GH_TOKEN"] = tok                       # read-only; never the state/publish PAT
    env["EXPAND_PARAMS"] = json.dumps(fanout_node.get("expand", {}))
    # Nested-fanout live wiring: surface the enclosing sub-pipeline's PREDECESSOR
    # sub-state evidence path (e.g. `main-review` for a `findings` fanout) so an
    # expander that derives items from a prior phase's evidence can read it. Best
    # effort, nested-only, and only when the evidence actually exists — a top-level
    # fanout (NODE_PATH of length 1) or a missing predecessor leaves it unset. This
    # is a computed PATH, not a secret, so it does not weaken the token allowlist.
    node_path_str = os.environ.get("NODE_PATH", "")
    if node_path_str and "." in node_path_str:
        try:
            with open(proto_path) as _pf:
                _proto = json.load(_pf)
            tp = node_path_str.split(".")
            seq_node = _paths.node_at_path(_proto, tp[:-1])
            sub_ids = [s["id"] for s in (seq_node.get("states", []) if seq_node else [])]
            if tp[-1] in sub_ids and sub_ids.index(tp[-1]) > 0:
                prev_id = sub_ids[sub_ids.index(tp[-1]) - 1]
                prev_ev = output_artifact_path(dir_, pid, instance,
                                               path=state_path(_proto, tp[:-1] + [prev_id]))
                if os.path.isfile(prev_ev):
                    env["EXPAND_PRIOR_EVIDENCE_PATH"] = prev_ev
        except Exception:
            pass  # best effort; the expander fails loud if it genuinely needs this
    r = subprocess.run([path, dir_, instance], text=True, capture_output=True, env=env)
    if r.returncode != 0:
        raise ValueError(f"expander '{expand.get('hook')}' failed (exit {r.returncode}): {r.stderr.strip()}")
    try:
        parsed = json.loads(r.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        raise ValueError(f"expander '{expand.get('hook')}' returned non-JSON: {r.stdout[:200]!r}")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        raise ValueError(f"expander '{expand.get('hook')}' output missing 'items' array")
    return parsed["items"]


def state_by_id(protocol, state_id):
    """Return the state dict with the given id, or None."""
    for s in protocol.get("states", []):
        if s.get("id") == state_id:
            return s
    return None


def _fanout_state(protocol):
    for s in protocol.get("states", []):
        if s.get("kind") == "fanout":
            return s
    return None  # unchanged: still returns the FIRST top-level fanout


def is_subpipeline_branch(branch_cfg):
    """True iff the branch entry is a linear sub-pipeline (has `states`)."""
    return bool(branch_cfg) and bool(branch_cfg.get("states"))


def branch_config(protocol, branch):
    """The branch entry dict from the protocol's fanout state, or None."""
    fo = _fanout_state(protocol)
    return _paths.child_by_id(fo.get("branches", []), branch) if fo else None


def branch_substates(protocol, branch):
    """Ordered list of sub-state dicts for a sub-pipeline branch ([] if flat)."""
    cfg = branch_config(protocol, branch)
    return list(cfg.get("states", [])) if is_subpipeline_branch(cfg) else []


def next_substate_id(protocol, branch, substate):
    """Id of the sub-state following `substate`, or None if it is the last."""
    fo = _fanout_state(protocol)
    return _paths.next_sibling(protocol, [fo["id"], branch, substate]) if fo else None


def branch_output_substate(protocol, branch):
    """The last sub-state id of a sub-pipeline branch (its leg output), else None."""
    subs = branch_substates(protocol, branch)
    return subs[-1]["id"] if subs else None


def state_inputs(protocol, state_id):
    """The `inputs` list declared on a top-level state OR a branch sub-state."""
    st = state_by_id(protocol, state_id)
    if st is not None:
        return list(st.get("inputs", []))
    fo = _fanout_state(protocol)
    if fo:
        for b in fo.get("branches", []):
            for s in b.get("states", []):
                if s.get("id") == state_id:
                    return list(s.get("inputs", []))
    return []


def _branch_ids(protocol):
    """Extract branch IDs from the fanout state."""
    fo = _fanout_state(protocol)
    return [b["id"] for b in fo.get("branches", [])] if fo else []


def _resolve_input_ref_pathaware(protocol, d, pid, instance, consuming_path, frm):
    """Path-aware (depth-4+) single-`from` resolution, nearest-scope-first
    (innermost enclosing sequence outward) relative to the consuming node's tree
    path. Walks UP the enclosing sequences; in each scope it scans the sequence's
    child states for a direct sibling match, and scans any child fanout's branches
    for a nested-leg match. Returns {path, kind} or None.

      - direct sibling sub-state F → output_artifact_path(state_path(proto, scope+[F]))
        kind = 'answers' if F is a gate, else 'evidence'.
      - leg F of a child fanout (scope+[fanoutid]) → its leg-output:
          flat leg          → state_path(proto, scope+[fanoutid, F])
          sub-pipeline leg  → its branch_output_substate appended.
        kind = 'evidence' (a leg output is always evidence).
    """
    scope = _paths.parent_path(consuming_path)
    while True:
        children = (_paths.children(protocol, scope) if scope
                    else protocol.get("states", []))
        for c in children:
            cid = c.get("id")
            if cid == frm:
                cpath = scope + [frm]
                kind = "answers" if _paths.node_kind(protocol, cpath) == "gate" else "evidence"
                return {"path": output_artifact_path(d, pid, instance,
                                                     path=state_path(protocol, cpath),
                                                     kind=kind),
                        "kind": kind}
            if c.get("kind") == "fanout":
                fo_path = scope + [cid]
                for br in c.get("branches", []):
                    if br.get("id") == frm:
                        leg_path = fo_path + [frm]
                        if is_subpipeline_branch(br):
                            last = br.get("states", [])[-1]["id"]
                            leg_path = leg_path + [last]
                        return {"path": output_artifact_path(d, pid, instance,
                                                             path=state_path(protocol, leg_path),
                                                             kind="evidence"),
                                "kind": "evidence"}
        if not scope:
            return None
        scope = _paths.parent_path(scope)


def resolve_inputs(protocol, d, pid, instance, consuming_branch, consuming_phase,
                   inputs, consuming_path=None):
    """Map each {from, as} to {as, path, kind}.

    When `consuming_path` (a tree-navigation path list) is given, resolution is
    PATH-AWARE: each `from` is resolved OUTERMOST-search relative to the consuming
    node's enclosing scopes (direct sibling sub-state, then a leg of a sibling
    nested fanout, walking up to the top). This is the depth-4+ path that lets a
    nested agent's inputs reach an earlier nested-fanout leg's evidence. Anything
    unresolved falls through to the legacy 3-case resolution below (so a top-level
    branch/phase `from` still works from a deep consumer).

    Legacy (consuming_path=None) resolution order for `from`:
      1) a sub-state of the consuming branch  → that sub-state's evidence
      2) a fanout branch id                   → that branch's leg-output evidence
                                                 (last sub-state, or the flat leg)
      3) a phase id                           → that phase's evidence
    `kind` is 'evidence' unless the source sub-state is a gate (then 'answers').

    Depth-<=3 results (paths + kind) are BYTE-IDENTICAL to the legacy function:
    when consuming_path is None the path-aware branch is never taken."""
    phase = consuming_phase or None
    out = []
    sub_ids = {s["id"]: s for s in branch_substates(protocol, consuming_branch)} if consuming_branch else {}
    branch_ids = set(_branch_ids(protocol))
    for ref in inputs:
        frm, as_ = ref["from"], ref["as"]
        if consuming_path is not None:
            r = _resolve_input_ref_pathaware(protocol, d, pid, instance, consuming_path, frm)
            if r is not None:
                out.append({"as": as_, "path": r["path"], "kind": r["kind"]})
                continue
        if frm in sub_ids:
            kind = "answers" if sub_ids[frm].get("kind") == "gate" else "evidence"
            path = output_artifact_path(d, pid, instance, branch=consuming_branch,
                                        phase=phase, substate=frm, kind=kind)
        elif frm in branch_ids:
            kind = "evidence"
            last = branch_output_substate(protocol, frm)
            path = output_artifact_path(d, pid, instance, branch=frm, phase=phase,
                                        substate=last, kind="evidence")
        else:
            path = output_artifact_path(d, pid, instance, phase=frm, kind="evidence")
            kind = "evidence"
            out.append({"as": as_, "path": path, "kind": kind})
            continue
        out.append({"as": as_, "path": path, "kind": kind})
    return out


def resolve_agent_unit_path(protocol, path):
    """Canonical: resolve the agent unit for the leaf at `path`."""
    node = _paths.node_at_path(protocol, path)
    if node is None:
        raise ValueError(f"no node at path {'.'.join(path)}")
    life = _paths.enclosing_fanout_id(protocol, path)
    return {"agent_state": path[-1],
            "max_iterations": node.get("max_iterations"),
            "life_state": life if life is not None else path[-1]}


def phase_states(protocol):
    """The ordered list of 'phase' states — those of kind agent or fanout.
    (join/deterministic states are transitions/terminals, not phases.)"""
    return [s for s in protocol.get("states", []) if s.get("kind") in ("agent", "fanout")]


def pipeline_states(protocol):
    """Ordered agent|fanout|GATE states — the full human-visible pipeline.
    Used ONLY by the status renderer. phase_states() stays agent|fanout so the
    agent-unit / seed / join logic is unaffected by gates."""
    return [s for s in protocol.get("states", []) if s.get("kind") in ("agent", "fanout", "gate")]


def is_multiphase(protocol):
    """A protocol is multi-phase iff it has more than one agent|fanout phase.
    Single-phase protocols (a lone agent, or a single fanout phase) keep the
    legacy layout + code paths untouched."""
    return len(phase_states(protocol)) > 1


def match_trigger(protocol, event_name, action="", comment_body="", is_pr_comment=True):
    """Map an ENTRY GitHub event to an engine command via protocol["triggers"].
    For issue_comment, a trigger's `target` (default "pr") must match whether the
    comment is on a PR (is_pr_comment True) or a plain issue (False)."""
    for t in protocol.get("triggers", []):
        if t.get("on") != event_name:
            continue
        if event_name == "issue_comment":
            want = "pr" if is_pr_comment else "issue"
            if t.get("target", "pr") != want:
                continue
            prefix = t.get("comment_prefix", "")
            if not prefix or comment_body.startswith(prefix):
                return t.get("command", "")
        elif event_name == "pull_request":
            actions = t.get("actions", [])
            if not actions or action in actions:
                return t.get("command", "")
        else:
            # generic event (e.g. workflow_dispatch): match on `on` alone.
            return t.get("command", "")
    return ""


def command_prefix(protocol, command, default=""):
    """Return the `comment_prefix` of the first trigger that maps to `command`,
    or `default` if no such trigger declares one. Lets the engine strip the
    protocol-configured prefix (e.g. /answer, /clarify) from a command's comment
    body instead of a hardcoded literal — so the answer-comment syntax stays
    per-protocol, not coupled to any one protocol's chosen verb."""
    for t in protocol.get("triggers", []):
        if t.get("command") == command and t.get("comment_prefix"):
            return t["comment_prefix"]
    return default


def agent_workflow(protocol, phase="", branch="", substate=""):
    """Resolve the gh-aw agent workflow basename for a leg.
    phase set + fanout phase -> that branch's workflow;
    phase set + agent phase  -> the phase state's workflow;
    branch only (single-phase fanout) -> that branch's workflow;
    neither -> the first agent state's workflow. "" if unresolved.
    substate set + sub-pipeline branch -> that sub-state's workflow."""
    if phase:
        st = state_by_id(protocol, phase)
        if st and st.get("kind") == "fanout":
            for b in st.get("branches", []):
                if b["id"] == branch:
                    if substate and "states" in b:
                        sub = next((s for s in b["states"] if s.get("id") == substate), None)
                        return (sub or {}).get("workflow", "")
                    return b.get("workflow", "")
            return ""
        return (st or {}).get("workflow", "")
    if branch:
        for st in protocol.get("states", []):
            if st.get("kind") == "fanout":
                for b in st.get("branches", []):
                    if b["id"] == branch:
                        if substate and "states" in b:
                            sub = next((s for s in b["states"] if s.get("id") == substate), None)
                            return (sub or {}).get("workflow", "")
                        return b.get("workflow", "")
        return ""
    for st in protocol.get("states", []):
        if st.get("kind") == "agent":
            return st.get("workflow", "")
    return ""


def route(protocols_dir, event_name, action="", comment_body="",
          dispatch_protocol="", is_pr_comment=True):
    """Pick the protocol to run for an incoming event by scanning all
    protocols/*/protocol.json `triggers` blocks. Protocol-agnostic router core.

    Returns {"protocol": <path>, "command": <cmd>, "skip": <bool>}:
      - repository_dispatch (dispatch_protocol set): the dispatch carries the
        protocol NAME (advance.py sends pid; protocol-join.yml rebuilds the path
        the same way), so reconstruct <protocols_dir>/<name>/protocol.json — the
        engine needs a path to open. No scan; command re-derived from the type.
      - entry event (pull_request / issue_comment): glob protocols in sorted
        order, run match_trigger on each (forwarding is_pr_comment so a comment's
        trigger `target` pr/issue must match a PR vs a plain issue); 0 matches ->
        skip, exactly 1 -> route, >=2 -> raise ValueError (ambiguous; the router
        job then fails loudly).
    """
    if dispatch_protocol:
        return {"protocol": os.path.join(protocols_dir, dispatch_protocol, "protocol.json"),
                "command": "", "skip": False}
    matches = []
    for path in sorted(glob.glob(os.path.join(protocols_dir, "*", "protocol.json"))):
        with open(path) as f:
            proto = json.load(f)
        cmd = match_trigger(proto, event_name, action, comment_body,
                            is_pr_comment=is_pr_comment)
        if cmd:
            matches.append((path, cmd))
    if not matches:
        return {"protocol": "", "command": "", "skip": True}
    if len(matches) > 1:
        names = ", ".join(p for p, _ in matches)
        # Describe WHAT collided in the trigger's own terms, not the raw GitHub
        # event/action (e.g. "issue_comment/created" hides that the comment text
        # "/review" is the thing two protocols both matched).
        if event_name == "issue_comment":
            what = f'the comment "{comment_body}"'
        elif event_name == "pull_request":
            what = f'pull_request action "{action}"'
        else:
            what = f'event "{event_name}"'
        raise ValueError(
            f"ambiguous route: {what} matches {len(matches)} protocols "
            f"({names}); their triggers overlap - make them mutually exclusive "
            f"(no comment_prefix may be a prefix of another protocol's)")
    path, cmd = matches[0]
    return {"protocol": path, "command": cmd, "skip": False}


def pr_from_instance(instance):
    """Derive the PR/issue NUMBER from an instance key.
    pr-<N> and issue-<N> -> <N> (the GitHub thread number, numeric so the engine
    can comment/label on it). ref-*/ui-* and any other shape pass through verbatim
    (no numeric thread)."""
    for prefix in ("pr-", "issue-"):
        if instance.startswith(prefix):
            return instance[len(prefix):]
    return instance


def instance_file(d, pid, instance):
    """instance_file <dir> <protocol-id> <instance-key> — shared per-instance bookkeeping."""
    return f"{d}/{pid}/{instance}/_instance.yaml"


def issue_question_body(pid, instance, gate_id, questions):
    """The body of an interactive-gate question issue: a machine marker (so the
    answer comment can be routed back to this run) + a parseable YAML block of the
    questions (for a UI to render) + the /answer instructions."""
    marker = f"<!-- agentic-mm: protocol={pid} instance={instance} gate={gate_id} -->"
    qlines = "\n".join(f"  - id: {q['id']}\n    text: {json.dumps(q['text'])}" for q in questions)
    eg = questions[0]["id"] if questions else "q1"
    return (
        f"{marker}\n\n"
        f"## Open questions — answer to resume mental-model recovery (`{instance}`)\n\n"
        f"```yaml\nquestions:\n{qlines}\n```\n\n"
        f"Reply with one or more `/answer <id>: <value>` lines in a single comment, "
        f"e.g. `/answer {eg}: …`. The run resumes automatically and this issue is "
        f"closed once every question is answered."
    )


def open_gate(dir_, pid, instance, proto_path, gate_id, sha, pr, branch=None, questions=None,
              phase=None, path=None, channel="comment"):
    """Seed a gate state file (gates.state=open), emit the awaiting check-run, and
    refresh the status comment. `branch` scopes the gate to a sub-pipeline leg.
    `phase` qualifies the path for multi-phase fan-out legs (e.g. review.B.clarify.yaml).
    `path` is the canonical FILE-NAMING path (already converted via state_path); when
    given it takes precedence over branch/phase/gate_id for the state file and check-run
    name. `questions` (a list of {id,text}) turns this into a data-carrying gate whose
    comment lists them with the /answer syntax. `channel="issue"` opens a dedicated
    GitHub issue (for ref/UI-keyed runs that have no PR) instead of posting to `pr`,
    and records its number on `gates.issue`. Caller owns the cursor + cas_push."""
    if path is not None:
        sf = state_file(dir_, pid, instance, path=path)
        # Build check-run name from path segments: pid + path elements joined by "/"
        cr_name = pid + "/" + "/".join(path)
    elif branch:
        sf = state_file(dir_, pid, instance, branch=branch, substate=gate_id, phase=phase)
        cr_name = f"{pid}/{branch}/{gate_id}"
    else:
        sf = state_file(dir_, pid, instance, phase=gate_id)
        cr_name = f"{pid}/{gate_id}"
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    gates = {"state": "open", "history": []}
    if questions:
        gates["questions"] = questions
    if questions and channel == "issue":
        # Interactive (no-PR) gate: open a dedicated question issue; the answer
        # comment is routed back via the marker in its body (mm-interactive-resume.yml).
        num = create_issue(f"Mental model — open questions ({instance})",
                           issue_question_body(pid, instance, gate_id, questions))
        gates["channel"] = "issue"
        if num:
            gates["issue"] = num
        dump_yaml(sf, {"protocol": pid, "instance": instance, "state": gate_id,
                       "head_sha": sha, "gates": gates})
        set_check_run(cr_name, sha, "in_progress", "", "Awaiting answers",
                      f"Answer the questions on issue #{num or '(created)'} with `/answer <id>: <value>`.")
        return
    dump_yaml(sf, {
        "protocol": pid, "instance": instance, "state": gate_id,
        "head_sha": sha, "gates": gates,
    })
    if questions:
        # Use the protocol's CONFIGURED answer-command prefix (e.g. /mm-answer), not a
        # hardcoded /answer — do_answer strips that same per-protocol prefix, so a gate
        # whose protocol registers a non-/answer verb would otherwise instruct a command
        # that routes to nothing and the gate would sit forever.
        try:
            ans = command_prefix(json.load(open(proto_path)), "answer", "/answer")
        except (OSError, ValueError):
            ans = "/answer"
        listed = "\n".join(f"{i+1}. `{q['id']}` — {q['text']}" for i, q in enumerate(questions))
        summary = (f"Answer with `{ans} <id>: <value>` (one or more per comment), e.g. "
                   f"`{ans} {questions[0]['id']}: …`.")
        set_check_run(cr_name, sha, "in_progress", "", "Awaiting answers", summary)
        post_pr_comment(pr, f"❓ **{gate_id}** needs input:\n\n{listed}\n\n{summary}")
    else:
        set_check_run(cr_name, sha, "in_progress", "", "Awaiting human approval",
                      "Comment `/approve`, `/request-changes`, or `/reject` on this PR.")
    inf = instance_file(dir_, pid, instance)
    if os.path.isfile(inf):
        body = render_pipeline_status_body(dir_, pid, instance, proto_path)
        upsert_status_comment(inf, pr, body)


def state_checkout(dir_):
    """state_checkout <dir> — clone the state branch; create it on origin if missing."""
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", STATE_REMOTE, STATE_BRANCH],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        subprocess.run(
            ["git", "clone", "-q", "--branch", STATE_BRANCH, "--single-branch", STATE_REMOTE, dir_],
            check=True, text=True
        )
    else:
        subprocess.run(["git", "init", "-q", "--initial-branch", STATE_BRANCH, dir_], check=True, text=True)
        git(dir_, "remote", "add", "origin", STATE_REMOTE)
        git(dir_, *GIT_ID, "commit", "-q", "--allow-empty", "-m", "init agentic-state")
        git(dir_, "push", "-q", "origin", STATE_BRANCH)


def cas_push(dir_, msg, attempts=5):
    """Commit everything and push fast-forward-only, retrying via rebase up to
    `attempts` times. NEVER force-push. A genuinely empty commit is a bug → fail."""
    import time
    git(dir_, *GIT_ID, "add", "-A")
    # An empty commit here means the engine pushed without changing state — a bug; fail loudly.
    staged = subprocess.run(["git", "-C", dir_, "diff", "--cached", "--quiet"]).returncode
    if staged == 0:
        sys.stderr.write("[engine] cas_push: nothing staged — refusing empty commit\n")
        sys.exit(1)
    git(dir_, *GIT_ID, "commit", "-q", "-m", msg)
    for i in range(attempts):
        r = subprocess.run(["git", "-C", dir_, "push", "-q", "origin", STATE_BRANCH])
        if r.returncode == 0:
            return
        sys.stderr.write(f"[engine] CAS push rejected (attempt {i+1}/{attempts}), rebasing\n")
        git(dir_, *GIT_ID, "pull", "-q", "--rebase", "origin", STATE_BRANCH)
        if i + 1 < attempts:
            time.sleep(0.1 * (i + 1))
    sys.stderr.write("[engine] CAS push failed after retries\n")
    sys.exit(1)


def resolve_executable(sdir, name, pdir, ex=""):
    """
    resolve_executable <search-dir> <name> <protocol-dir> <explicit-exec-or-empty>
    Prints OK\t<path> or ERR\t<reason> to stdout.
    """
    if ex:
        path = f"{pdir}/{ex}"
        if os.path.isfile(path):
            return f"OK\t{path}"
        else:
            return f"ERR\tdeclared exec not found: {ex}"

    # Extension-agnostic: match <sdir>/<name> or <sdir>/<name>.*
    matches = []
    exact = f"{sdir}/{name}"
    if os.path.isfile(exact):
        matches.append(exact)
    # glob for extensions
    for g in sorted(glob.glob(f"{sdir}/{name}.*")):
        if os.path.isfile(g):
            matches.append(g)

    if len(matches) == 0:
        return f"ERR\tno executable found (looked for {sdir}/{name} or {sdir}/{name}.*)"
    elif len(matches) > 1:
        return f"ERR\tambiguous: multiple files match {sdir}/{name}.* ({' '.join(matches)}); use an explicit \"exec\""
    else:
        return f"OK\t{matches[0]}"


def set_check_run(name, sha, status, conclusion, title, summary):
    """
    set_check_run <name> <head_sha> <status> <conclusion-or-empty> <title> <summary>
    Best-effort: failure never breaks a transition.
    """
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(
            f"[ENGINE_LOCAL] check-run {name} sha={sha} status={status} "
            f"conclusion={conclusion or 'none'} title={title} summary={summary}\n"
        )
        return
    if not sha:
        sys.stderr.write("[engine] no head sha; skipping check run\n")
        return
    args = [
        "-f", f"name={name}",
        "-f", f"head_sha={sha}",
        "-f", f"status={status}",
        "-f", f"output[title]={title}",
        "-f", f"output[summary]={summary}",
    ]
    if conclusion:
        args += ["-f", f"conclusion={conclusion}"]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    # Check-runs must be created by the ACTIONS token (github-actions[bot]) — a
    # classic PAT cannot create/supersede an Actions-app check-run. Prefer a
    # dedicated CHECK_RUN_TOKEN (the workflow's GITHUB_TOKEN, which the job grants
    # `checks: write`); fall back to PUBLISH_TOKEN for callers whose PUBLISH_TOKEN
    # already IS the Actions token (advance/join jobs). This matters for a protocol
    # that finalizes at a terminal `merge` in the plan job, where PUBLISH_TOKEN is
    # the dispatch PAT (which can post the review but cannot complete the check-run).
    check_token = os.environ.get("CHECK_RUN_TOKEN") or os.environ.get("PUBLISH_TOKEN", "")
    env = dict(os.environ)
    if check_token:
        env["GH_TOKEN"] = check_token
    result = subprocess.run(
        ["gh", "api", "-X", "POST", f"repos/{repo}/check-runs"] + args,
        text=True, capture_output=True, env=env
    )
    if result.returncode != 0:
        sys.stderr.write(
            "[engine] check-run create failed (needs checks:write + Actions token; "
            f"merge-gating needs branch protection): {result.stderr.strip()}\n"
        )


# --- Phase labels -----------------------------------------------------------
# Engine-level head keys that are NOT protocol states. Protocols may override
# any of these via a top-level "phase_labels" map in protocol.json.
PHASE_LABEL_DEFAULTS = {
    "setup": "⚙ setup",
    "done": "✅ done",
    "failed": "❌ failed",
    "blocked": "⛔ blocked",
}
PHASE_LABEL_COLOR = "5319e7"  # one color for every engine-managed phase label


def _humanize_state_id(state_id):
    return state_id.replace("-", " ").replace("_", " ").strip().capitalize()


def phase_label_text(protocol, key):
    """Resolve a state id OR a terminal/special key to a PR label string.

    Live phase (key matches a states[] id): the state's `label` if present, else
    a humanized id. Terminal/special key (setup/done/failed/blocked): the
    protocol's optional top-level `phase_labels[key]` override if present, else
    the engine default. `protocol` is the parsed protocol JSON dict.
    """
    st = state_by_id(protocol, key)
    if st is not None:
        return st.get("label") or _humanize_state_id(key)
    overrides = protocol.get("phase_labels", {}) or {}
    if key in overrides:
        return overrides[key]
    return PHASE_LABEL_DEFAULTS.get(key, _humanize_state_id(key))


def _gh_label_cmd(args):
    """Run a best-effort `gh` command for labels/PR-edit. Returns (ok, stderr).
    Never raises. Uses PUBLISH_TOKEN (as GH_TOKEN) + GITHUB_REPOSITORY."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    env = dict(os.environ)
    token = os.environ.get("PUBLISH_TOKEN", "")
    if token:
        env["GH_TOKEN"] = token
    try:
        result = subprocess.run(
            ["gh"] + args + (["--repo", repo] if repo else []),
            text=True, capture_output=True, env=env,
        )
        return result.returncode == 0, result.stderr
    except Exception as e:  # gh missing, etc. — never break a transition
        return False, str(e)


def _ensure_and_add_label(text, pr):
    """Ensure the label exists (idempotent --force create) then add it to the PR.
    Best-effort. ENGINE_LOCAL → log only."""
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] add-label pr={pr}: {text}\n")
        return
    if not str(pr).isdigit():   # ref-/UI-targeted run: no PR to label
        return
    # gh pr edit --add-label errors on a nonexistent label, so create-first.
    _gh_label_cmd(["label", "create", text, "--color", PHASE_LABEL_COLOR, "--force"])
    ok, err = _gh_label_cmd(["pr", "edit", str(pr), "--add-label", text])
    if not ok:
        sys.stderr.write(f"[engine] add-label failed for '{text}': {err}\n")


def remove_pr_label(pr, label):
    """Best-effort remove one label from the PR. ENGINE_LOCAL → log only."""
    if not label:
        return
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] remove-label pr={pr}: {label}\n")
        return
    if not str(pr).isdigit():   # ref-/UI-targeted run: no PR to label
        return
    _gh_label_cmd(["pr", "edit", str(pr), "--remove-label", label])


def apply_setup_label(protocol, pr):
    """Add the engine 'setup' label to the PR. Best-effort, no state tracking —
    called before _instance.yaml exists. ensure_phase_label removes it later."""
    _ensure_and_add_label(phase_label_text(protocol, "setup"), pr)


def ensure_phase_label(dir_, pid, instance, protocol, pr, head_key):
    """Reconcile the PR's phase label to `head_key`.

    Reads the applied label from _instance.yaml; if it differs from the resolved
    new text, removes {prev} ∪ {setup-label} and adds the new one; records the
    new text back on _instance.yaml. No-op when there is no _instance.yaml (this
    excludes the single-agent v1 path). Best-effort. ENGINE_LOCAL → log + still
    record state. The CALLER cas_pushes the instance file."""
    inf = instance_file(dir_, pid, instance)
    if not os.path.isfile(inf):
        return
    try:
        inst = load_yaml(inf) or {}
        new = phase_label_text(protocol, head_key)
        prev = inst.get("phase_label", "") or ""
        if prev == new:
            return
        setup_text = phase_label_text(protocol, "setup")
        if os.environ.get("ENGINE_LOCAL", "0") == "1":
            sys.stderr.write(f"[ENGINE_LOCAL] phase-label {instance}: {prev or '∅'} → {new}\n")
            inst["phase_label"] = new
            dump_yaml(inf, inst)
            return
        for old in {prev, setup_text}:
            if old and old != new:
                remove_pr_label(pr, old)
        _ensure_and_add_label(new, pr)
        inst["phase_label"] = new
        dump_yaml(inf, inst)
    except Exception as e:
        sys.stderr.write(f"[engine] ensure_phase_label failed (non-fatal): {e}\n")


def match_run_by_cid(runs_json, cid):
    """
    match_run_by_cid <runs-json> <cid>
    Pure resolver: finds the databaseId whose displayTitle contains the delimited
    token "cid:[<cid>]". Returns the id as a string, or empty string if none match.
    """
    needle = f"cid:[{cid}]"
    try:
        runs = json.loads(runs_json)
    except json.JSONDecodeError:
        return ""
    for run in runs:
        title = run.get("displayTitle") or ""
        if needle in title:
            return str(run["databaseId"])
    return ""


def join_policy_satisfied(policy, done, total):
    """Is a dynamic join's barrier satisfied given `done` legs out of `total`?
      all (default) : every leg done (vacuously true when total==0)
      any           : >=1 leg done (false when total==0)
      quorum:N      : >=N done, N an int count OR a percentage of total ('80%')
    Raises ValueError on an unparseable quorum."""
    policy = (policy or "all").strip()
    if policy == "all":
        return done == total
    if policy == "any":
        return done >= 1
    if policy.startswith("quorum:"):
        spec = policy[len("quorum:"):].strip()
        if spec.endswith("%"):
            try:
                pct = float(spec[:-1])
            except ValueError:
                raise ValueError(f"unparseable quorum percentage: {policy!r}")
            need = math.ceil(total * pct / 100.0)
        else:
            try:
                need = int(spec)
            except ValueError:
                raise ValueError(f"unparseable quorum count: {policy!r}")
        return done >= need
    raise ValueError(f"unknown join policy: {policy!r}")


def decide(results, iterations_remaining):
    """Pure fold: (check verdicts + severities) → (process, blocking).

    process  ∈ {"done","iterate","failed"} — the process axis that drives the
             iterate loop and terminal state.
    blocking : bool — did a `block`-severity check fail (the conclusion-axis
             input; no consumer yet — the M2 phase-gate reads it).

    Severity is each verdict's "on_fail" (default "iterate" when absent, so
    pre-severity verdicts and the single-agent regression path are unchanged).
    `iterate`-severity failures drive the loop; `block` failures never iterate
    but set blocking; `advisory` failures are recorded only. Zero verdicts is a
    checks-job failure → treated as a failed attempt.

    Callers must stamp `on_fail` onto each verdict from the protocol's check
    entry before calling (see run-checks.py); absent it, every failure defaults
    to `iterate` (v1 behavior).
    """
    if not results:
        return ("iterate" if iterations_remaining else "failed"), False
    def sev(v):
        return v.get("on_fail", "iterate")
    iterate_fail = any(not v.get("pass") and sev(v) == "iterate" for v in results)
    block_fail = any(not v.get("pass") and sev(v) == "block" for v in results)
    if iterate_fail:
        process = "iterate" if iterations_remaining else "failed"
    else:
        process = "done"
    return process, block_fail


def upsert_status_comment(sf, pr, body):
    """
    upsert_status_comment <state_file> <pr> <body>
    Single engine-owned PR comment, edited in place; id persisted in state.
    Mutates the state file but does NOT push.
    """
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] status comment pr#{pr}: {body}\n")
        return
    # Only a real (numeric) PR has a comment thread. Ref-/UI-targeted runs have
    # no PR — and the engine derives `pr` from the instance there (e.g. "ui-e2e3"),
    # which is non-empty — so gate on isdigit, not emptiness. Status for ref runs is
    # served by the visibility API. (The gh call below uses check=True and would
    # raise on repos/<r>/issues/<non-numeric>/comments.)
    if not str(pr).isdigit():
        return

    state = load_yaml(sf)
    cid = state.get("status_comment_id", "") or ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    publish_token = os.environ.get("PUBLISH_TOKEN", "")
    env = dict(os.environ)
    if publish_token:
        env["GH_TOKEN"] = publish_token

    if not cid:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{pr}/comments",
             "-f", f"body={body}", "--jq", ".id"],
            text=True, capture_output=True, env=env, check=True
        )
        new_cid = result.stdout.strip()
        state["status_comment_id"] = int(new_cid) if new_cid.isdigit() else new_cid
        dump_yaml(sf, state)
    else:
        subprocess.run(
            ["gh", "api", "-X", "PATCH",
             f"repos/{repo}/issues/comments/{cid}",
             "-f", f"body={body}"],
            text=True, capture_output=True, env=env, check=True
        )


def post_pr_comment(pr, body):
    """
    post_pr_comment <pr> <body>
    Post a NEW (untracked) PR/issue comment — used for one-off engine notices
    (e.g. HITL override announcements and refusals). Unlike upsert_status_comment
    it does not track or edit an id. Best-effort; ENGINE_LOCAL short-circuits.
    """
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] pr comment pr#{pr}: {body}\n")
        return
    if not str(pr).isdigit():   # ref-/UI-targeted run: no real PR thread
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    publish_token = os.environ.get("PUBLISH_TOKEN", "")
    env = dict(os.environ)
    if publish_token:
        env["GH_TOKEN"] = publish_token
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{pr}/comments", "-f", f"body={body}"],
        text=True, capture_output=True, env=env,
    )
    if result.returncode != 0:
        sys.stderr.write(f"[engine] pr comment post failed (needs issues:write): {result.stderr.strip()}\n")


def _gh_env():
    env = dict(os.environ)
    tok = os.environ.get("PUBLISH_TOKEN", "")
    if tok:
        env["GH_TOKEN"] = tok
    return env


def create_issue(title, body):
    """Open a GitHub issue; return its number as a string (or "" on failure).
    Used by interactive (no-PR) question gates. Best-effort; ENGINE_LOCAL → log +
    return a stub number so the gate still records an issue locally."""
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] create issue: {title}\n")
        return "0"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues", "-f", f"title={title}",
         "-f", f"body={body}", "--jq", ".number"],
        text=True, capture_output=True, env=_gh_env(),
    )
    if r.returncode != 0:
        sys.stderr.write(f"[engine] create issue failed (needs issues:write): {r.stderr.strip()}\n")
        return ""
    return r.stdout.strip()


def close_issue(number, comment=""):
    """Comment (optional) then close an issue. Best-effort; ENGINE_LOCAL → log."""
    if not str(number).strip():
        return
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] close issue #{number}: {comment}\n")
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    env = _gh_env()
    if comment:
        subprocess.run(["gh", "api", f"repos/{repo}/issues/{number}/comments",
                        "-f", f"body={comment}"], text=True, capture_output=True, env=env)
    r = subprocess.run(["gh", "api", "-X", "PATCH", f"repos/{repo}/issues/{number}",
                        "-f", "state=closed"], text=True, capture_output=True, env=env)
    if r.returncode != 0:
        sys.stderr.write(f"[engine] close issue failed (needs issues:write): {r.stderr.strip()}\n")


def finalize_superseded_comment(pr, cid, body):
    """One-time edit of an ABANDONED status comment on reset: PATCH the comment
    `cid` to `body` (a superseded banner prepended above its frozen final state),
    then never touch it again — the caller drops status_comment_id so the next
    run creates a fresh comment. Best-effort: a failure (e.g. the comment was
    deleted) is logged, not fatal, so it never aborts the reset. ENGINE_LOCAL
    short-circuits (and logs, so tests can assert the call)."""
    if not cid:
        return
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] supersede comment {cid} pr#{pr}: {body}\n")
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    publish_token = os.environ.get("PUBLISH_TOKEN", "")
    env = dict(os.environ)
    if publish_token:
        env["GH_TOKEN"] = publish_token
    result = subprocess.run(
        ["gh", "api", "-X", "PATCH", f"repos/{repo}/issues/comments/{cid}",
         "-f", f"body={body}"],
        text=True, capture_output=True, env=env,
    )
    if result.returncode != 0:
        sys.stderr.write(f"[engine] supersede comment {cid} failed (non-fatal): {result.stderr.strip()}\n")


def render_fanout_status_body(dir_, pid, instance, proto):
    """
    render_fanout_status_body <state_dir> <pid> <instance> <protocol.json>
    Pure projection of ALL fan-out branch state files into ONE combined PR-comment body.
    """
    branch_val = os.environ.get("STATE_BRANCH", STATE_BRANCH)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    link = f"https://github.com/{repo}/tree/{branch_val}/{pid}/{instance}"

    protocol = load_yaml(proto)

    # Find the fanout state and its legs. Static: the declared branches[]. Dynamic
    # (expand present): synthesize one leg per persisted manifest entry so the human
    # status comment renders dynamic legs (check-run gating already uses the manifest).
    branches = []
    for state in protocol.get("states", []):
        if state.get("kind") == "fanout":
            fo_id = state.get("id")
            if state.get("expand"):
                each = state.get("each", {})
                man = read_manifest(dir_, pid, instance, [fo_id])
                for leg in man.get("legs", []):
                    branches.append({"id": leg["id"],
                                     "max_iterations": each.get("max_iterations", "?")})
            else:
                for b in state.get("branches", []):
                    branches.append(b)
            break

    sections = ""
    states_list = []

    for b in branches:
        bid = b["id"]
        max_iter = b.get("max_iterations", "?")
        sf = state_file(dir_, pid, instance, bid)

        if os.path.isfile(sf):
            branch_state = load_yaml(sf)
            history = branch_state.get("history", []) or []
            st = branch_state.get("state", "") or ""

            if history:
                lines_list = []
                for entry in history:
                    it = entry.get("iteration", "?")
                    fb = entry.get("feedback", "") or ""
                    if fb == "":
                        lines_list.append(f"- ✅ iteration {it}/{max_iter} — all checks passed")
                    else:
                        lines_list.append(f"- ✗ iteration {it}/{max_iter} — {fb}")
                lines = "\n".join(lines_list)
            else:
                lines = "_no iterations yet_"
        else:
            lines = "_pending_"
            st = "pending"

        states_list.append(st)
        sections += f"**{bid}**\n\n{lines}\n\n"

    # Headline from branch states
    any_active = False
    any_failed = False
    for st in states_list:
        if st == "done":
            pass
        elif st == "failed":
            any_failed = True
        else:
            any_active = True

    if any_active:
        headline = "⏳ Review in progress…"
    elif any_failed:
        headline = "❌ Review incomplete — a branch could not complete; merge is gated."
    else:
        headline = "✅ Review complete — published."

    return f"\U0001f50d **{pid} · {instance}**\n\n{sections}{headline}\n\n[Full state & audit trail]({link})\n"


DEFAULT_MAX_DEPTH = 5


def effective_max_depth(proto):
    """Return the protocol's configured max_depth, or DEFAULT_MAX_DEPTH if unset."""
    v = proto.get("max_depth")
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else DEFAULT_MAX_DEPTH


def check_depth(proto):
    """Raise ValueError if the protocol's static tree depth exceeds the cap."""
    d = _paths.max_static_depth(proto)
    cap = effective_max_depth(proto)
    if d > cap:
        raise ValueError(f"protocol depth {d} exceeds max_depth {cap}")


def _validate_sequence(states, path_hint):
    """Walk a list of state dicts (a sequence at `path_hint`) and raise ValueError
    with an actionable message + the offending node id for each authoring rule:

    Rule 1 — join.of unknown fanout in scope
        A join's `of` must name a fanout sibling in the SAME sequence.
        Rationale: join and its fanout are always siblings at the same tree level
        (deep-fanout: join-analyze.of="analyze" are both in the "deep" sub-pipeline).

    Rule 2 — agent/flat-branch missing workflow
        Every `kind:agent` state OR flat fanout branch (a branch dict without
        `states`) must carry a `workflow` key.

    Rule 3 — gate.questions_from nonexistent sibling
        A gate's `questions_from` (when set) must refer to another state id in
        the same enclosing sequence.

    Rule 4 — fanout branches[] XOR expand+each (dynamic fan-out)
        A fanout has exactly one of a static `branches[]` or a dynamic
        `expand`+`each` pair. `expand` must carry hook/as/id_from/max_legs
        (max_legs an int in [1,256]); `expand.matrix_fields`, when present, must
        be an array of non-empty strings (the subset of item keys inlined into
        matrix.leg.inputs — unset means the full item, see project_matrix_item).
        `each` is a flat leg (`workflow`) XOR a sub-pipeline (`states`),
        validated recursively like a static branch.

    Rule 5 — join.policy must parse
        A join's optional `policy` must be accepted by `join_policy_satisfied`
        ('all', 'any', or 'quorum:<N|P%>').

    Rule 6 — merge input from_fanout unknown fanout in scope
        A merge input's `from_fanout` (when present) must name a fanout
        sibling in the SAME sequence, mirroring Rule 1.
    """
    # Collect ids and fanout ids visible in this sequence for rule 1.
    sibling_ids = {s.get("id") for s in states if s.get("id")}
    fanout_ids = {s.get("id") for s in states if s.get("kind") == "fanout"}

    for st in states:
        sid = st.get("id", "<unnamed>")
        kind = st.get("kind", "")

        # Rule 2a — top-level agent state missing workflow
        if kind == "agent" and not st.get("workflow"):
            raise ValueError(
                f"agent node '{sid}' missing 'workflow' — add a \"workflow\": \"<name>\" "
                f"key to the '{sid}' state"
            )

        # Rule 1 — join references unknown fanout (+ policy validity)
        if kind == "join":
            of = st.get("of", "")
            if of and of not in fanout_ids:
                raise ValueError(
                    f"join '{sid}' references unknown fanout of='{of}' — "
                    f"make sure a fanout with id='{of}' exists as a sibling of '{sid}'"
                )
            pol = st.get("policy")
            if pol is not None:
                try:
                    join_policy_satisfied(pol, 0, 0)  # parse-check only
                except ValueError:
                    raise ValueError(
                        f"join '{sid}' has invalid policy='{pol}' — use "
                        f"'all', 'any', or 'quorum:<N|P%>'"
                    )

        # Rule 3 — gate.questions_from nonexistent sibling
        if kind == "gate":
            qf = st.get("questions_from", "")
            if qf and qf not in sibling_ids:
                raise ValueError(
                    f"gate '{sid}' has questions_from='{qf}' but no sibling state "
                    f"with id='{qf}' exists — add the source state or correct the name"
                )

        # Rule 6 — merge.from_fanout must name a fanout in scope
        if kind == "merge":
            for inp in st.get("inputs", []) or []:
                ff = inp.get("from_fanout")
                if ff and ff not in fanout_ids:
                    raise ValueError(
                        f"merge '{sid}' input from_fanout='{ff}' names no fanout in scope — "
                        f"make sure a fanout with id='{ff}' exists as a sibling of '{sid}'"
                    )

        # Recurse into fanout branches / validate dynamic expand+each
        if kind == "fanout":
            has_static = bool(st.get("branches"))
            has_dynamic = bool(st.get("expand")) or bool(st.get("each"))
            if has_static == has_dynamic:
                raise ValueError(
                    f"fanout '{sid}' must have exactly one of branches[] (static) "
                    f"or expand+each (dynamic) — not both, not neither"
                )
            if has_dynamic:
                exp = st.get("expand") or {}
                for req in ("hook", "as", "id_from", "max_legs"):
                    if not exp.get(req) and exp.get(req) != 0:
                        raise ValueError(
                            f"fanout '{sid}' expand missing '{req}' — expand needs "
                            f"hook, as, id_from, and max_legs"
                        )
                ml = exp.get("max_legs")
                if not isinstance(ml, int) or isinstance(ml, bool) or not (1 <= ml <= 256):
                    raise ValueError(
                        f"fanout '{sid}' expand.max_legs must be an int in [1,256], got {ml!r}"
                    )
                mf = exp.get("matrix_fields")
                if mf is not None and (not isinstance(mf, list) or not all(isinstance(x, str) and x for x in mf)):
                    raise ValueError(
                        f"fanout '{sid}' expand.matrix_fields must be an array of non-empty strings"
                    )
                each = st.get("each") or {}
                if bool(each.get("states")) == bool(each.get("workflow")):
                    raise ValueError(
                        f"fanout '{sid}' each must be a flat leg (workflow) XOR a "
                        f"sub-pipeline (states) — not both, not neither"
                    )
                if each.get("states"):
                    _validate_sequence(each["states"], path_hint + [sid, "each"])
            else:
                for br in st.get("branches", []):
                    bid = br.get("id", "<unnamed>")
                    if br.get("states"):
                        # sub-pipeline branch — recurse into its states
                        _validate_sequence(br["states"], path_hint + [bid])
                    else:
                        # flat branch (implicit agent) — must have workflow (Rule 2b)
                        if not br.get("workflow"):
                            raise ValueError(
                                f"agent node '{bid}' missing 'workflow' — add a "
                                f"\"workflow\": \"<name>\" key to the '{bid}' branch"
                            )


def validate_protocol(proto):
    """Validate a parsed protocol dict for common authoring errors.

    Raises ValueError with an actionable message naming the offending node id
    for each of the following high-value rules:
      - join.of references a fanout not in scope (same sequence)
      - agent node (top-level or flat fanout branch) missing 'workflow'
      - gate.questions_from names a nonexistent sibling sub-state
      - merge input's from_fanout references a fanout not in scope (same sequence)

    Intentionally does NOT validate: check file existence, schema references,
    trigger syntax, or anything that requires disk access — those belong in
    check/run-checks resolution, not here. Keep this rule set small (YAGNI).
    """
    _validate_sequence(proto.get("states", []), [])


def has_fanout(protocol):
    """True iff the protocol has at least one fan-out state."""
    return any(s.get("kind") == "fanout" for s in protocol.get("states", []))


def _render_leg_section(sf, max_iter):
    """Project one leg's state file into (state, checklist-lines).
    Mirrors the per-branch rendering in render_fanout_status_body so the
    single-phase and multi-phase comments read identically per leg.
      missing file        → ("pending", "_pending_")
      file, empty history → (<state>, "_no iterations yet_")
      file, with history  → (<state>, "- ✅/✗ iteration n/m …")
    """
    if not os.path.isfile(sf):
        return "pending", "_pending_"
    data = load_yaml(sf)
    history = data.get("history", []) or []
    st = data.get("state", "") or ""
    if not history:
        return st, "_no iterations yet_"
    out = []
    for entry in history:
        it = entry.get("iteration", "?")
        fb = entry.get("feedback", "") or ""
        # `feedback` carries only iterate-severity failures, so a gate that fails
        # a block/advisory check leaves it empty. Fall back to the recorded checks
        # map so we never claim "all checks passed" when a non-iterate check failed.
        failed = [k for k, v in (entry.get("checks", {}) or {}).items() if v != "pass"]
        if fb:
            out.append(f"- ✗ iteration {it}/{max_iter} — {fb}")
        elif failed:
            out.append(f"- ⚠️ iteration {it}/{max_iter} — checks failed: {', '.join(sorted(failed))}")
        else:
            out.append(f"- ✅ iteration {it}/{max_iter} — all checks passed")
    return st, "\n".join(out)


def _evidence_status_note(d, pid, instance, ph_id, bid, cfg):
    """Render a flagged note for a fan-out leg's status header from its evidence —
    driven ENTIRELY by the fanout's `params.status_note` config so the generic engine
    carries no protocol vocabulary. The per-leg checklist reports 'all checks passed'
    from the FORM checks only, so a leg whose evidence carries a flag-worthy verdict /
    severity reads as clear without this. cfg keys (all optional):
      verdict_field + flag_verdicts[]   → flag when ev[verdict_field] ∈ flag_verdicts
      severity_field + flag_severities[]→ count findings[].<severity_field> ∈ flag_severities
      label (default "flagged"), emoji (default "⚠️").
    Returns '' when cfg is absent (callers pass it only for opted-in fanouts), the
    evidence is missing/malformed, or nothing matched.
    """
    if not isinstance(cfg, dict):
        return ""
    path = output_artifact_path(d, pid, instance, branch=bid, phase=ph_id, kind="evidence")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path) as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        return ""
    if not isinstance(ev, dict):
        return ""
    vfield, flag_verdicts = cfg.get("verdict_field"), cfg.get("flag_verdicts") or []
    sfield, flag_sev = cfg.get("severity_field"), cfg.get("flag_severities") or []
    counts = {}
    if sfield and flag_sev:
        for f in (ev.get("findings") if isinstance(ev.get("findings"), list) else []):
            if isinstance(f, dict) and f.get(sfield) in flag_sev:
                counts[f[sfield]] = counts.get(f[sfield], 0) + 1
    verdict_flagged = bool(vfield and ev.get(vfield) in flag_verdicts)
    if not verdict_flagged and not counts:
        return ""
    parts = [f"{counts[s]} {s}" for s in flag_sev if counts.get(s)]
    detail = f" ({', '.join(parts)})" if parts else ""
    return f" — {cfg.get('emoji', '⚠️')} {cfg.get('label', 'flagged')}{detail}"


def render_pipeline_status_body(dir_, pid, instance, proto):
    """
    render_pipeline_status_body <state_dir> <pid> <instance> <protocol.json>
    Protocol-LEVEL projection for a MULTI-PHASE protocol: render every phase
    (agent + fan-out) in declared order into ONE PR-comment body. Unlike
    render_fanout_status_body (single fan-out phase, <instance>/<branch>.yaml),
    this resolves each leg with its phase id, so fan-out legs are found at
    <instance>/<phase>.<branch>.yaml — the fix for PR #65's stuck "_pending_".
    The audit link points at the instance directory (all phases live under it).
    """
    branch_val = os.environ.get("STATE_BRANCH", STATE_BRANCH)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    link = f"https://github.com/{repo}/tree/{branch_val}/{pid}/{instance}"

    protocol = load_yaml(proto)
    inf = instance_file(dir_, pid, instance)
    inst = load_yaml(inf) if os.path.isfile(inf) else {}
    overridden = {o.get("phase") for o in (inst.get("overrides") or [])}
    halted = inst.get("halted") or {}
    halted_phase = halted.get("phase") if halted.get("reason") == "blocked" else None

    sections = ""
    any_active = any_failed = False
    gate_open = False
    blocked_phase = None

    for ph in pipeline_states(protocol):
        ph_id = ph["id"]
        if ph.get("kind") == "fanout":
            for b in ph.get("branches", []):
                bid = b["id"]
                max_iter = b.get("max_iterations", "?")
                sf = state_file(dir_, pid, instance, bid, phase=ph_id)
                st, lines = _render_leg_section(sf, max_iter)
                vnote = _evidence_status_note(dir_, pid, instance, ph_id, bid,
                                              (ph.get("params") or {}).get("status_note"))
                sections += f"**{ph_id} · {bid}**{vnote}\n\n{lines}\n\n"
                if st == "done":
                    pass
                elif st == "failed":
                    any_failed = True
                else:  # pending / in-flight
                    any_active = True
        elif ph.get("kind") == "gate":
            sf = state_file(dir_, pid, instance, phase=ph_id)
            if not os.path.isfile(sf):
                continue  # gate not reached yet → no row (pre-gate output unchanged)
            g = (load_yaml(sf).get("gates") or {})
            gstate = g.get("state", "")
            hist = g.get("history") or []
            who = (hist[-1].get("actor") if hist else "") or ""
            if gstate == "approved":
                note = f"✅ approved by @{who}"
            elif gstate == "rejected":
                note = f"⛔ rejected by @{who}"
                any_failed = True
            elif gstate == "changes_requested":
                note = f"🔁 changes requested by @{who} — push a fix or `/approve`"
                gate_open = True
            else:  # open
                note = "⏳ awaiting human sign-off (`/approve` · `/request-changes` · `/reject`)"
                gate_open = True
            sections += f"**{ph_id}**\n\n{note}\n\n"
        else:  # agent phase
            max_iter = ph.get("max_iterations", "?")
            sf = state_file(dir_, pid, instance, phase=ph_id)
            st, lines = _render_leg_section(sf, max_iter)
            if ph_id == halted_phase:
                note = "\n⛔ blocked — a required gate did not pass; a write-access user can `/override`."
                blocked_phase = ph_id
            elif ph_id in overridden:
                note = "\n⚠️ blocked → overridden; proceeding."
            elif st == "done":
                note = "\n✅ clear."
            elif st == "failed":
                note = "\n❌ failed."
                any_failed = True
            else:  # pending / in-flight
                note = ""
                if st != "done":
                    any_active = True
            sections += f"**{ph_id}**\n\n{lines}\n{note}\n\n"

    if blocked_phase:
        headline = (f"⛔ Blocked at **{blocked_phase}** — a write-access user can comment "
                    f"`/override <reason>` to proceed past this gate.")
    elif gate_open:
        headline = ("⏳ Awaiting human approval — comment `/approve`, "
                    "`/request-changes`, or `/reject`.")
    elif any_failed:
        headline = "❌ Pipeline failed — a gate could not complete; merge is gated."
    elif any_active:
        headline = "⏳ In progress…"
    else:
        headline = "✅ Pipeline complete — published."

    return f"\U0001f50d **{pid} · {instance}**\n\n{sections}{headline}\n\n[Full state & audit trail]({link})\n"


def render_instance_status_body(dir_, pid, instance, proto_path):
    """Pick the right shared-comment renderer for an instance-keyed comment:
    multi-phase → the protocol-level pipeline renderer; single-phase fan-out →
    the legacy fan-out renderer (kept byte-identical)."""
    protocol = load_yaml(proto_path)
    if is_multiphase(protocol):
        return render_pipeline_status_body(dir_, pid, instance, proto_path)
    return render_fanout_status_body(dir_, pid, instance, proto_path)


def ensure_status_comment(state_dir, pid, instance, proto_path, pr):
    """
    ensure_status_comment <state_dir> <pid> <instance> <protocol.json> <pr>
    Create-once guard for the shared instance-level status comment.  Reads the
    instance file's status_comment_id; if empty → render + upsert + cas_push;
    if already set → no-op.  Now also fires for a multi-phase protocol whose
    FIRST phase is an agent (e.g. preflight), so the protocol-level comment +
    audit link appear the moment the pipeline starts. A single-agent protocol
    (no fan-out, not multi-phase) has no shared comment → no-op.
    """
    protocol = load_yaml(proto_path)
    if not is_multiphase(protocol) and not has_fanout(protocol):
        return  # single-agent path: status lives in the per-state file, no shared comment
    inf = instance_file(state_dir, pid, instance)
    inst_data = load_yaml(inf) if os.path.isfile(inf) else {}
    cid = inst_data.get("status_comment_id", "") or ""
    if cid:
        # Already created on a previous run — idempotent no-op.
        return
    body = render_instance_status_body(state_dir, pid, instance, proto_path)
    upsert_status_comment(inf, pr, body)
    cas_push(state_dir, f"{instance}: ensure shared status comment")


def _gh_dispatch(event_type, fields):
    """Fire a repository_dispatch. ENGINE_LOCAL → no-op (logs to stderr in gh-args
    format). Retries up to 3 attempts (5s apart) on gh api failure (e.g. a 403 from
    the shared PAT's rate limit, or a transient 5xx); after the 3rd failure raises
    so the calling job fails red instead of stalling silently. State is CAS-pushed
    before dispatch at every call site, so a red job here is safely recoverable by
    re-dispatch."""
    import time
    args = [f"repos/{os.environ.get('GITHUB_REPOSITORY', '')}/dispatches",
            "-f", f"event_type={event_type}"]
    for k, v in fields.items():
        args += ["-F", f"client_payload[{k}]={v}"]
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] gh api {' '.join(args)}\n")
        return
    last_err = ""
    for attempt in range(3):
        r = subprocess.run(["gh", "api"] + args, text=True, capture_output=True)
        if r.returncode == 0:
            return
        last_err = r.stderr
        if attempt < 2:
            time.sleep(5)
    sys.stderr.write(f"[engine] repository_dispatch {event_type} failed after 3 attempts: {last_err}\n")
    raise RuntimeError(f"[engine] repository_dispatch {event_type} failed after 3 attempts: {last_err}")


def dispatch_continue(pid, instance, branch=None, substate=None, phase="", path=None):
    """Dispatch a protocol-continue event to resume a sub-pipeline leg.
    `path` (dot-joined tree path) drives the recursive NODE_PATH continue guard
    for NESTED legs; when set it is sent alone. The legacy branch/substate/phase
    form (depth-<=3) is byte-identical."""
    if path:
        _gh_dispatch("protocol-continue", {"protocol": pid, "instance": instance, "path": path})
        return
    f = {"protocol": pid, "instance": instance, "branch": branch, "substate": substate}
    if phase:
        f["phase"] = phase
    _gh_dispatch("protocol-continue", f)


def fire_join_dispatch(pid, instance, fanout_path=""):
    """Dispatch a protocol-join event (all legs done; trigger the join barrier).
    `fanout_path` (dot-joined TREE path of the enclosing fanout) is carried as
    client_payload[path] ONLY for a NESTED fanout; the TOP fanout stays path-less
    (byte-identical to the legacy behavior)."""
    f = {"protocol": pid, "instance": instance}
    if fanout_path:
        f["path"] = fanout_path
    _gh_dispatch("protocol-join", f)


def materialize_inputs(resolved, target_dir):
    """Copy each existing resolved input to <target_dir>/inputs/<as>.json.
    Returns [{as, staged_path}] for the ones that existed."""
    inputs_dir = os.path.join(str(target_dir), "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    manifest = []
    for r in resolved:
        if not os.path.isfile(r["path"]):
            continue
        dst = os.path.join(inputs_dir, f"{r['as']}.json")
        shutil.copyfile(r["path"], dst)
        manifest.append({"as": r["as"], "staged_path": dst})
    return manifest


def stage_item(dir_, pid, instance, file_path, as_, item):
    """Persist a dynamic leg's item beside its state file as
    <...>.<as>.item.json, so the dispatch/materialize step can surface it as
    inputs/<as>.json for the leg's agent. Keyed by the leg's file-naming path."""
    dst = output_artifact_path(dir_, pid, instance, path=file_path, kind=f"{as_}.item")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w") as f:
        json.dump(item, f)


def project_matrix_item(item, matrix_fields):
    """Subset a dynamic leg's item to the keys that ride the GHA matrix.
    matrix_fields None/unset -> the full item (backward-compatible). Absent keys
    are skipped. The FULL item always stays durable on the state branch (stage_item);
    this only trims what is inlined into matrix.leg.inputs."""
    if not matrix_fields:
        return item
    return {k: item[k] for k in matrix_fields if k in item}


# GHA strategy.matrix / $GITHUB_OUTPUT practical ceiling; keep well under 1 MB.
_MATRIX_BYTES_CAP = 900_000


def check_matrix_size(legs):
    """Fail loud if the serialized matrix legs would exceed the GHA output/matrix
    cap. A protocol author who forgot `matrix_fields` gets a clear error, never a
    silent truncation (same discipline as max_legs over-cap)."""
    n = len(json.dumps(legs))
    if n > _MATRIX_BYTES_CAP:
        raise ValueError(
            f"matrix legs serialize to {n} bytes (> {_MATRIX_BYTES_CAP}); "
            f"set the fanout's expand.matrix_fields to inline only small keys "
            f"(large fields stay on the state branch; the agent re-fetches them)")


def run_merge_hook(dir_, pid, instance, proto_path, merge_state, consuming_path=None):
    """Resolve+materialize a merge state's inputs and run its trusted reduce hook.
    Returns {conclusion, summary}; neutral fallback on any resolution/exec error.

    `consuming_path` is the merge node's TREE path. For a NESTED merge (a per-file
    `reduce` inside a sub-pipeline leg — path length > 1), a `from_fanout` resolves
    RELATIVE to that path: the fanout is the merge's sibling in the same
    (sub-)sequence, i.e. `consuming_path[:-1] + [fanout_id]`; plain `from` inputs
    resolve path-aware from the same scope. For the TOP merge (consuming_path None
    or length 1) resolution is byte-identical to the pre-nesting behavior: the
    fanout is the top-level `[fanout_id]` and plain inputs use the legacy 3-case
    resolver (consuming_path suppressed)."""
    pdir = os.path.dirname(os.path.abspath(proto_path))
    with open(proto_path) as f:
        proto = json.load(f)
    fo = _fanout_state(proto)
    phase = fo["id"] if (fo and is_multiphase(proto)) else None
    merge_inputs = merge_state.get("inputs", [])
    # A nested merge (its tree path has more than one element) resolves its
    # from_fanout relative to its own scope; `nested` still gates that below.
    nested = bool(consuming_path) and len(consuming_path) > 1
    # Resolve plain `from` inputs PATH-AWARE whenever the merge's node path is known
    # (top merge included), mirroring run_conclude_hook — so a merge can read a leg of
    # ANY fanout, not just the first. Byte-identical to legacy for a first-fanout
    # branch. (No protocol used a top merge before, so this only affects new users.)
    cp_for_inputs = consuming_path
    # from_fanout inputs have no `from` key — resolve_inputs only understands
    # `from`, so keep them out of that call and handle them in the loop below.
    plain_inputs = [inp for inp in merge_inputs if "from" in inp]
    # Branch-id refs resolve against branch leg outputs (Plan 2 resolve_inputs).
    resolved = resolve_inputs(proto, dir_, pid, instance,
                              consuming_branch=None, consuming_phase=phase,
                              inputs=plain_inputs, consuming_path=cp_for_inputs)
    workdir = tempfile.mkdtemp(prefix="merge-")
    materialize_inputs(resolved, workdir)
    for inp in merge_inputs:
        if inp.get("from_fanout"):
            fo_id = inp["from_fanout"]
            # Resolve the fanout RELATIVE TO the merge's node-path: it is the
            # merge's sibling in the same (sub-)sequence → parent-of-merge + fanout
            # id. Top merge → the top fanout ([fo_id]).
            if nested:
                fo_tree_path = list(consuming_path[:-1]) + [fo_id]
            else:
                fo_tree_path = [fo_id]
            # A nested fanout is NOT a top-level state, so state_by_id() would miss
            # it — address it by full tree path.
            fo_node = _paths.node_at_path(proto, fo_tree_path)
            if fo_node is None or not os.path.isfile(manifest_file(dir_, pid, instance, fo_tree_path)):
                raise ValueError(
                    f"merge from_fanout='{fo_id}': no manifest at {'.'.join(fo_tree_path)} "
                    f"(fanout not materialized or misnamed)"
                )
            rows = collect_fanout_evidence(dir_, pid, instance, fo_tree_path, fo_node, proto=proto)
            inputs_dir = os.path.join(workdir, "inputs")
            os.makedirs(inputs_dir, exist_ok=True)
            with open(os.path.join(inputs_dir, f"{inp['as']}.json"), "w") as f:
                json.dump(rows, f)
    res = resolve_executable(f"{pdir}/publish", merge_state.get("hook", ""), pdir, "")
    kind, path = res.split("\t", 1)
    if kind == "ERR" or not os.access(path, os.X_OK):
        sys.stderr.write(f"[merge] hook unresolved/not-exec: {path}\n")
        return {"conclusion": "neutral", "summary": "merge hook unresolved"}
    # The trusted hook posts its combined PR comment via lib.post_pr_comment, which
    # reads PR from the env. In the unified engine the merge runs from next.py in the
    # PLAN job, which does not set PR (pre-4a it ran in protocol-join.yml, which did),
    # so derive PR from the instance for the hook subprocess. setdefault keeps any
    # PR the job already provides. (Live-found: combine merge comment silently dropped.)
    hook_env = dict(os.environ)
    hook_env.setdefault("PR", instance[len("pr-"):] if instance.startswith("pr-") else instance)
    r = subprocess.run([path, workdir, instance], text=True, capture_output=True, env=hook_env)
    if r.returncode != 0:
        sys.stderr.write(f"[merge] hook nonzero: {r.stderr}\n")
        return {"conclusion": "neutral", "summary": "merge hook failed"}
    try:
        parsed = json.loads(r.stdout.strip())
        if isinstance(parsed, dict) and "conclusion" in parsed and "summary" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return {"conclusion": "neutral", "summary": "merge hook returned no verdict"}


def finalize_merge_result(res):
    """Map a merge hook's result to the (conclusion, summary, label) the TOP-merge
    finalize arm publishes. A hook that crashed / was unresolved / returned no
    verdict (conclusion 'neutral') must GATE, not complete: neutral -> failure with
    an explicit hook-failed summary and a 'failed' phase label. A genuine verdict
    ('success' or 'failure', e.g. an honest NOT-honest outcome) passes through with
    the 'done' label (the pipeline completed; the verdict is the verdict)."""
    res = res if isinstance(res, dict) else {}
    concl = res.get("conclusion", "neutral")
    summary = res.get("summary", "")
    if concl in ("success", "failure"):
        return concl, summary, "done"
    return "failure", f"verdict hook failed — gate blocked: {summary or 'no verdict'}", "failed"


def _cli(argv):
    if not argv:
        sys.stderr.write("lib.py: no subcommand given\n")
        sys.exit(2)
    cmd, args = argv[0], argv[1:]
    if cmd == "protocol-id":
        print(protocol_id(args[0]))
    elif cmd == "state-file":
        # state-file <dir> <pid> <instance> [branch] [phase]   (positional; pass "" for branch to get a phase-only path)
        print(state_file(*args))
    elif cmd == "instance-file":
        print(instance_file(*args))
    elif cmd == "set-check-run":
        # set-check-run <name> <sha> <status> <conclusion> <title> <summary>
        set_check_run(*args)
    elif cmd == "match-run-by-cid":
        # match-run-by-cid <runs-json> <cid>
        # args[0] = runs_json, args[1] = cid  (same order as the bash function)
        result = match_run_by_cid(args[0], args[1])
        if result:
            print(result)
    elif cmd == "render-fanout-status-body":
        # render-fanout-status-body <dir> <pid> <instance> <protocol.json>
        print(render_fanout_status_body(*args), end="")
    elif cmd == "upsert-status-comment":
        # upsert-status-comment <state_file> <pr> <body>
        upsert_status_comment(*args)
    elif cmd == "post-pr-comment":
        # post-pr-comment <pr> <body>
        post_pr_comment(args[0], args[1])
    elif cmd == "cas-push":
        # cas-push <dir> <message>
        cas_push(*args)
    elif cmd == "resolve-executable":
        # resolve-executable <sdir> <name> <pdir> [exec]
        ex = args[3] if len(args) > 3 else ""
        print(resolve_executable(args[0], args[1], args[2], ex))
    elif cmd == "state-checkout":
        state_checkout(args[0])
    elif cmd == "ensure-status-comment":
        # ensure-status-comment <state_dir> <pid> <instance> <protocol.json> <pr>
        ensure_status_comment(args[0], args[1], args[2], args[3], args[4])
    elif cmd == "match-trigger":
        # match-trigger <protocol.json> <event_name> <action> <comment_body> [is_pr_comment]
        # The 5th positional defaults to "true" (back-compat for 4-arg callers);
        # only "false" flips it (a comment on a plain issue, not a PR).
        with open(args[0]) as f:
            proto = json.load(f)
        ev = args[1] if len(args) > 1 else ""
        act = args[2] if len(args) > 2 else ""
        body = args[3] if len(args) > 3 else ""
        ispr = args[4] if len(args) > 4 else "true"
        print(match_trigger(proto, ev, act, body, is_pr_comment=(ispr.lower() != "false")))
    elif cmd == "agent-workflow":
        # agent-workflow <protocol.json> <phase> <branch> [substate]
        with open(args[0]) as f:
            proto = json.load(f)
        ph = args[1] if len(args) > 1 else ""
        br = args[2] if len(args) > 2 else ""
        sub = args[3] if len(args) > 3 else ""
        print(agent_workflow(proto, ph, br, sub))
    elif cmd == "route":
        # route <protocols_dir> <event_name> <action> <comment_body> <dispatch_protocol> <is_pr_comment>
        pdir = args[0]
        ev = args[1] if len(args) > 1 else ""
        act = args[2] if len(args) > 2 else ""
        body = args[3] if len(args) > 3 else ""
        disp = args[4] if len(args) > 4 else ""
        ispr = (args[5].lower() == "true") if len(args) > 5 else True
        try:
            r = route(pdir, ev, act, body, disp, ispr)
        except ValueError as e:
            sys.stderr.write(f"lib.py route: {e}\n")
            sys.exit(1)
        print(f"protocol={r['protocol']}")
        print(f"skip={'true' if r['skip'] else 'false'}")
    else:
        sys.stderr.write(f"lib.py: unknown subcommand {cmd}\n")
        sys.exit(2)


if __name__ == "__main__":
    _cli(sys.argv[1:])

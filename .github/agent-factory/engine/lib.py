#!/usr/bin/env python3
"""Engine shared library. Importable by the engine scripts AND a thin CLI
(`python3 lib.py <subcommand> ...`) for helpers the orchestrator calls inline."""
import glob
import json
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


def match_trigger(protocol, event_name, action="", comment_body=""):
    """Map an ENTRY GitHub event to an engine command via protocol["triggers"].
    Returns the command ("start"/"reset"/...) or "" if nothing matches (the
    workflow then no-ops). Internal re-entry dispatches (protocol-continue /
    protocol-join) are generic and NOT handled here."""
    for t in protocol.get("triggers", []):
        if t.get("on") != event_name:
            continue
        if event_name == "issue_comment":
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
      - issue_comment on a non-PR issue: skip (the engine ignores these anyway).
      - entry event (pull_request / PR issue_comment): glob protocols in sorted
        order, run match_trigger on each; 0 matches -> skip, exactly 1 -> route,
        >=2 -> raise ValueError (ambiguous; the router job then fails loudly).
    """
    if dispatch_protocol:
        return {"protocol": os.path.join(protocols_dir, dispatch_protocol, "protocol.json"),
                "command": "", "skip": False}
    if event_name == "issue_comment" and not is_pr_comment:
        return {"protocol": "", "command": "", "skip": True}
    matches = []
    for path in sorted(glob.glob(os.path.join(protocols_dir, "*", "protocol.json"))):
        with open(path) as f:
            proto = json.load(f)
        cmd = match_trigger(proto, event_name, action, comment_body)
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


def instance_file(d, pid, instance):
    """instance_file <dir> <protocol-id> <instance-key> — shared per-instance bookkeeping."""
    return f"{d}/{pid}/{instance}/_instance.yaml"


def open_gate(dir_, pid, instance, proto_path, gate_id, sha, pr, branch=None, questions=None,
              phase=None, path=None):
    """Seed a gate state file (gates.state=open), emit the awaiting check-run, and
    refresh the status comment. `branch` scopes the gate to a sub-pipeline leg.
    `phase` qualifies the path for multi-phase fan-out legs (e.g. review.B.clarify.yaml).
    `path` is the canonical FILE-NAMING path (already converted via state_path); when
    given it takes precedence over branch/phase/gate_id for the state file and check-run
    name. `questions` (a list of {id,text}) turns this into a data-carrying gate whose
    comment lists them with the /answer syntax. Caller owns the cursor + cas_push."""
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
    dump_yaml(sf, {
        "protocol": pid, "instance": instance, "state": gate_id,
        "head_sha": sha, "gates": gates,
    })
    if questions:
        listed = "\n".join(f"{i+1}. `{q['id']}` — {q['text']}" for i, q in enumerate(questions))
        summary = ("Answer with `/answer <id>: <value>` (one or more per comment), e.g. "
                   f"`/answer {questions[0]['id']}: …`.")
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
    publish_token = os.environ.get("PUBLISH_TOKEN", "")
    env = dict(os.environ)
    if publish_token:
        env["GH_TOKEN"] = publish_token
    result = subprocess.run(
        ["gh", "api", "-X", "POST", f"repos/{repo}/check-runs"] + args,
        text=True, capture_output=True, env=env
    )
    if result.returncode != 0:
        sys.stderr.write(
            "[engine] check-run create failed (needs checks:write + Actions token; "
            "merge-gating needs branch protection)\n"
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

    # Find the fanout state and its branches
    branches = []
    for state in protocol.get("states", []):
        if state.get("kind") == "fanout":
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

        # Rule 1 — join references unknown fanout
        if kind == "join":
            of = st.get("of", "")
            if of and of not in fanout_ids:
                raise ValueError(
                    f"join '{sid}' references unknown fanout of='{of}' — "
                    f"make sure a fanout with id='{of}' exists as a sibling of '{sid}'"
                )

        # Rule 3 — gate.questions_from nonexistent sibling
        if kind == "gate":
            qf = st.get("questions_from", "")
            if qf and qf not in sibling_ids:
                raise ValueError(
                    f"gate '{sid}' has questions_from='{qf}' but no sibling state "
                    f"with id='{qf}' exists — add the source state or correct the name"
                )

        # Recurse into fanout branches
        if kind == "fanout":
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
                sections += f"**{ph_id} · {bid}**\n\n{lines}\n\n"
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
    """Fire a repository_dispatch. ENGINE_LOCAL → no-op (logs to stderr in gh-args format)."""
    args = [f"repos/{os.environ.get('GITHUB_REPOSITORY', '')}/dispatches",
            "-f", f"event_type={event_type}"]
    for k, v in fields.items():
        args += ["-F", f"client_payload[{k}]={v}"]
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] gh api {' '.join(args)}\n")
        return
    subprocess.run(["gh", "api"] + args, text=True, capture_output=True)


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


def run_merge_hook(dir_, pid, instance, proto_path, merge_state):
    """Resolve+materialize a merge state's inputs and run its trusted reduce hook.
    Returns {conclusion, summary}; neutral fallback on any resolution/exec error."""
    pdir = os.path.dirname(os.path.abspath(proto_path))
    with open(proto_path) as f:
        proto = json.load(f)
    fo = _fanout_state(proto)
    phase = fo["id"] if (fo and is_multiphase(proto)) else None
    # Branch-id refs resolve against branch leg outputs (Plan 2 resolve_inputs).
    resolved = resolve_inputs(proto, dir_, pid, instance,
                              consuming_branch=None, consuming_phase=phase,
                              inputs=merge_state.get("inputs", []))
    workdir = tempfile.mkdtemp(prefix="merge-")
    materialize_inputs(resolved, workdir)
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
        # match-trigger <protocol.json> <event_name> <action> <comment_body>
        with open(args[0]) as f:
            proto = json.load(f)
        ev = args[1] if len(args) > 1 else ""
        act = args[2] if len(args) > 2 else ""
        body = args[3] if len(args) > 3 else ""
        print(match_trigger(proto, ev, act, body))
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

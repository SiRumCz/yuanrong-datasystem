#!/usr/bin/env python3
"""advance.py <state_workdir> <instance-key> <protocol.json> <verdicts.json> <evidence.json>
The ONLY writer of non-initial state. The iterate/done/failed decision is the pure
lib.decide() fold over verdict severities. Reads check verdicts (never agent files,
except evidence for publication AFTER checks passed), mutates state, CAS-pushes,
and performs the consequent action: publish / re-dispatch / fail loudly.
Tolerates a missing state file (recovers from a lost init, e.g. a plan job
that failed after dispatch) by starting at {state: review, iteration: 1, history: []}.
Env: AGENT_RUN_ID, GITHUB_REPOSITORY, PUBLISH_TOKEN (reviews+comments),
     GH_TOKEN (repository_dispatch), ENGINE_LOCAL.
"""
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import typing

# Import shared library from the same directory as this script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib


@dataclasses.dataclass
class LegCtx:
    """The stable identity of the leg being advanced, grouped so the depth-N
    walk helpers (advance_node / complete_sequence / persist_output) take one
    context object instead of ~20 positional args. Everything here is fixed for
    the duration of one advance.py invocation; the situational bits (process,
    cur, the next-sibling kind) stay as explicit args where they vary."""
    dir_: str
    pid: str
    instance: str
    branch: str
    phase: str
    substate: str
    sf: str
    cursor_sf: str
    inf: str
    pr: str
    proto_path: str
    cr_name: str
    max_iter: typing.Any
    github_repository: str
    sha: str
    life_state: typing.Any
    tree_path: typing.Optional[list]
    file_path: typing.Optional[list]
    proto: dict


def _join_path(proto, tree_path):
    """Dot-joined path of the ENCLOSING fanout, but ONLY when it is NESTED
    (tree path length > 1); else "". Carried as fire_join's client_payload[path]
    so join.py evaluates the right barrier. The TOP fanout (length 1) and the
    legacy depth-<=3 path (tree_path is None) both yield "" → a path-less join,
    byte-identical to the legacy behavior."""
    if tree_path is None or proto is None:
        return ""
    import paths as _paths
    fp = _paths.enclosing_fanout_path(proto, tree_path)
    return ".".join(fp) if fp and len(fp) > 1 else ""


def persist_output(ctx, evid, kind="evidence"):
    """Copy the agent's artifact to its deterministic persisted path so
    downstream `inputs` can resolve it. Best-effort: a missing/empty evid is a
    no-op (the leg simply has no output to forward).

    `ctx.file_path` (NODE_PATH mode) is the canonical FILE-NAMING path (already
    routed through lib.state_path); when given it takes precedence over
    branch/phase/substate so a depth-4 leg persists to
    <deep.analyze.sec>.evidence.json."""
    if not evid or not os.path.isfile(evid):
        return
    if ctx.file_path is not None:
        dst = lib.output_artifact_path(ctx.dir_, ctx.pid, ctx.instance,
                                       path=ctx.file_path, kind=kind)
    else:
        dst = lib.output_artifact_path(ctx.dir_, ctx.pid, ctx.instance,
                                       branch=(ctx.branch or None),
                                       phase=(ctx.phase or None),
                                       substate=(ctx.substate or None), kind=kind)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(evid, dst)


def gh_api(*args):
    """Run 'gh api ...' with ENGINE_LOCAL short-circuit."""
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] gh api {' '.join(args)}\n")
        return
    result = subprocess.run(
        ["gh", "api"] + list(args),
        text=True, capture_output=True
    )
    if result.returncode != 0:
        sys.stderr.write(f"[engine] gh api failed: {result.stderr}\n")


def fire_join(pid, instance, branch, fanout_path=""):
    """On a TERMINAL branch (done OR failed), signal the fan-out barrier.
    No-op for the single-agent path (branch empty).

    `fanout_path` (dot-joined TREE path of the ENCLOSING fanout) is carried as
    client_payload[path] ONLY for a NESTED fanout (path length > 1); join.py
    (Task 12b) reads it to evaluate the right nested barrier. For the TOP fanout
    it is left empty so join.py's existing top-level evaluation is byte-identical.
    """
    if not branch:
        return
    args = [
        "repos/" + os.environ.get("GITHUB_REPOSITORY", "") + "/dispatches",
        "-f", "event_type=protocol-join",   # -f: literal string; -F would add JSON quoting
        "-F", f"client_payload[protocol]={pid}",
        "-F", f"client_payload[instance]={instance}",
    ]
    if fanout_path:
        args += ["-F", f"client_payload[path]={fanout_path}"]
    gh_api(*args)


def complete_sequence(ctx, cur):
    """Terminal action for the last sub-state of a done sub-pipeline leg.
    Marks the leg cursor done, emits a status comment, CAS-pushes, and fires join.
    Called from advance_node when the last sub-state of branch finishes successfully.

    When the enclosing fanout is NESTED (path length > 1) the join dispatch carries
    its path so join.py evaluates the right barrier; the TOP fanout (length 1) fires
    a path-less join — byte-identical to the legacy behavior."""
    cur["state"] = "done"             # last sub-state → leg terminal
    lib.dump_yaml(ctx.cursor_sf, cur)
    update_status_comment(ctx.sf, ctx.inf, ctx.branch, ctx.pr, ctx.pid, ctx.instance,
                          ctx.proto_path, ctx.dir_, "✅ done — published.",
                          ctx.max_iter, ctx.github_repository)
    lib.cas_push(ctx.dir_, f"{ctx.instance}: branch {ctx.branch} {ctx.substate} done → leg done")
    fire_join(ctx.pid, ctx.instance, ctx.branch, _join_path(ctx.proto, ctx.tree_path))


def advance_node(ctx, process):
    """Advance a sub-pipeline branch node.  Called when ``ctx.branch`` and
    ``ctx.substate`` are both set.

    process=='done':   If next sibling exists → seed/dispatch it (agent), open the
                       gate (gate kind), or — when the next sibling is a FANOUT —
                       re-dispatch protocol-continue with client_payload[path]=<fanout
                       tree path> (so next.py's `continue` enters the nested fanout)
                       WITHOUT seeding a leg file; else → complete_sequence (leg terminal).
    process=='failed': Mark the branch cursor failed so the join barrier can observe
                       the leg's outcome; the caller (main) handles the shared
                       check-run / status-comment / cas-push / fire-join.

    `ctx.tree_path` (NODE_PATH mode) is the canonical TREE path of the leaf being
    advanced (e.g. ["preflight","deep","triage"]). When set, sibling lookup +
    file naming route through paths.* / lib.state_path so depth-4 works; when None
    the legacy depth-<=3 branch/phase/substate behavior is byte-identical."""
    proto, proto_path, dir_ = ctx.proto, ctx.proto_path, ctx.dir_
    pid, instance, branch = ctx.pid, ctx.instance, ctx.branch
    phase, substate, cursor_sf = ctx.phase, ctx.substate, ctx.cursor_sf
    life_state, sha, pr = ctx.life_state, ctx.sha, ctx.pr
    github_repository, tree_path = ctx.github_repository, ctx.tree_path

    if process == "failed":
        cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
        cur["state"] = "failed"
        lib.dump_yaml(cursor_sf, cur)
        return

    # process == "done"
    import paths as _paths
    parent = _paths.parent_path(tree_path)
    nxt_sub = _paths.next_sibling(proto, tree_path)
    # Mark this sub-state's own file done (already set above), then move on.
    lib.set_check_run(ctx.cr_name, sha, "completed", "success",
                      f"{substate} complete", "")
    cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
    if nxt_sub:
        nxt_kind = _paths.node_kind(proto, parent + [nxt_sub])
        nxt_state = None

        # --- Next sibling is a FANOUT → enter it via protocol-continue. ---
        # The leg stays in-flight; we move the cursor onto the fanout id and let
        # next.py's `continue` (NODE_PATH=<fanout path>) seed the fanout's child
        # legs + nested __join.yaml. We deliberately do NOT seed a leg file here.
        if nxt_kind == "fanout":
            cur["sub_state"] = nxt_sub
            cur["state"] = life_state         # leg stays in flight
            lib.dump_yaml(cursor_sf, cur)
            fanout_tree_path = parent + [nxt_sub]
            lib.cas_push(dir_, f"{instance}: {'.'.join(tree_path)} done → fanout {nxt_sub}")
            gh_api(
                f"repos/{github_repository}/dispatches",
                "-f", "event_type=protocol-continue",
                "-F", f"client_payload[protocol]={pid}",
                "-F", f"client_payload[instance]={instance}",
                "-F", f"client_payload[path]={'.'.join(fanout_tree_path)}",
            )
            return

        cur["sub_state"] = nxt_sub
        cur["state"] = life_state         # leg stays in flight
        lib.dump_yaml(cursor_sf, cur)
        if nxt_kind == "gate":
            # Open the gate; read questions from the source sub-state's persisted
            # evidence.  Use path-aware file resolution (via lib.state_path) so
            # multi-phase protocols produce the correct filename (e.g.
            # review.B.clarify.yaml, not the legacy single-phase B.clarify.yaml).
            questions = []
            questions_known = False  # source evidence carried an EXPLICIT `questions` list
            qfrom = (_paths.node_at_path(proto, parent + [nxt_sub]) or {}).get("questions_from")
            if qfrom:
                qpath = lib.output_artifact_path(dir_, pid, instance,
                                                 path=lib.state_path(proto, parent + [qfrom]),
                                                 kind="evidence")
                if os.path.isfile(qpath):
                    try:
                        raw = json.load(open(qpath)).get("questions", None)
                    except (json.JSONDecodeError, ValueError):
                        raw = None
                    if isinstance(raw, list):
                        questions, questions_known = raw, True
            if qfrom and questions_known and not questions:
                # Auto-complete an empty DATA gate ONLY when the source agent deliberately
                # surfaced an EXPLICIT empty `questions` list — advance past it as if
                # resolved (→ gate.next, or leg-terminal → join). A missing / null / garbled
                # `questions` is an agent malfunction, not a decision to skip a HUMAN gate, so
                # fall through to open_gate (fail-closed: hold for a human) instead of silently
                # advancing. (The cursor sub_state was already set to the gate above.)
                gate_path = parent + [nxt_sub]
                gsf = lib.state_file(dir_, pid, instance,
                                     path=lib.state_path(proto, gate_path))
                lib.dump_yaml(gsf, {"protocol": pid, "instance": instance,
                                    "state": "done", "head_sha": sha,
                                    "gates": {"state": "auto-resolved", "history": []}})
                advance_node(dataclasses.replace(
                    ctx, substate=nxt_sub, tree_path=gate_path,
                    cr_name=pid + "/" + "/".join(gate_path[1:])), "done")
                return
            lib.open_gate(dir_, pid, instance, proto_path, nxt_sub, sha, pr,
                          questions=questions,
                          path=lib.state_path(proto, parent + [nxt_sub]))
            lib.cas_push(dir_, f"{instance}: branch {branch} {substate} done → gate {nxt_sub} open")
            return
        # Otherwise: an agent sub-state. Advance the cursor (done above) and
        # dispatch a path-continue; the continue's enter_node SEEDS the sub-state
        # file. We deliberately do NOT pre-seed it here — pre-seeding makes the
        # follow-on continue's cas_push an empty commit (identical content), which
        # aborts an agent→agent sub-pipeline transition. Mirrors do_answer's
        # gate→next-substate handling in next.py (which also leaves seeding to the
        # continue). The cursor sub_state change (above) is what this cas_push
        # commits.
        lib.cas_push(dir_, f"{instance}: branch {branch} {substate} done → {nxt_sub}")
        gh_api(
            f"repos/{github_repository}/dispatches",
            "-f", "event_type=protocol-continue",
            "-F", f"client_payload[protocol]={pid}",
            "-F", f"client_payload[instance]={instance}",
            "-F", f"client_payload[branch]={branch}",
            "-F", f"client_payload[substate]={nxt_sub}",
            "-F", f"client_payload[path]={'.'.join(parent + [nxt_sub])}",
        )
    else:
        complete_sequence(ctx, cur)


def run_publish_hook(proto_path, proto, branch, agent_state, evid, instance, pid):
    """Resolve and run the protocol's publish-state executable.
    Returns {conclusion, summary} dict; on any resolution/exec failure,
    returns a neutral conclusion so the transition still completes."""

    if branch:
        # fan-out branch: get .publish from the branch entry
        action = None
        for state in proto.get("states", []):
            if state.get("kind") == "fanout":
                for b in state.get("branches", []):
                    if b["id"] == branch:
                        action = b.get("publish") or None
                        break
                break
        exec_override = ""
    else:
        # single-agent: publish hook is on the state after agent_state (.next)
        pubstate_id = None
        for state in proto.get("states", []):
            if state.get("id") == agent_state:
                pubstate_id = state.get("next") or None
                break
        action = None
        exec_override = ""
        if pubstate_id:
            for state in proto.get("states", []):
                if state.get("id") == pubstate_id:
                    action = state.get("action") or None
                    exec_override = state.get("exec") or ""
                    break

    pdir = os.path.dirname(os.path.abspath(proto_path))

    if not action and not exec_override:
        return {"conclusion": "neutral", "summary": "no publish action defined"}

    res = lib.resolve_executable(f"{pdir}/publish", action or "", pdir, exec_override)
    kind, path = res.split("\t", 1)

    if kind == "ERR":
        sys.stderr.write(f"[advance] publish hook unresolved: {path}\n")
        return {"conclusion": "neutral", "summary": "publish hook unresolved"}

    if not os.access(path, os.X_OK):
        sys.stderr.write(f"[advance] publish hook not executable: {path}\n")
        return {"conclusion": "neutral", "summary": "publish hook not executable"}

    # The hook is trusted (zone 4) and inherits the full parent env
    # (ENGINE_LOCAL, PUBLISH_TOKEN, GITHUB_REPOSITORY, PR).
    result = subprocess.run(
        [path, evid, instance],
        text=True, capture_output=False,
        stdout=subprocess.PIPE, stderr=None
    )
    if result.returncode != 0:
        sys.stderr.write("[advance] publish hook exited nonzero\n")
        return {"conclusion": "neutral", "summary": "publish hook failed"}

    out = result.stdout.strip()
    try:
        parsed = json.loads(out)
        if isinstance(parsed, dict) and "conclusion" in parsed and "summary" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    return {"conclusion": "neutral", "summary": "publish hook returned no verdict"}


def run_conclude_hook(proto_path, proto, state_id, evid, instance, blocking,
                      dir_=None, tree_path=None):
    """Resolve+run the optional `conclude` hook for an agent state. Returns
    {conclusion,summary,blocked} or None if the state declares none. Trusted
    (zone 4). Receives BLOCKING via env and, for states with declared inputs,
    CONCLUDE_INPUTS_DIR with those inputs materialized as <as>.json."""
    state = lib.state_by_id(proto, state_id)
    action = (state or {}).get("conclude") or None
    if not action:
        return None
    pdir = os.path.dirname(os.path.abspath(proto_path))
    res = lib.resolve_executable(f"{pdir}/publish", action, pdir, "")
    kind, path = res.split("\t", 1)
    if kind == "ERR" or not os.access(path, os.X_OK):
        sys.stderr.write(f"[advance] conclude hook unresolved/not-exec: {path}\n")
        return {"conclusion": "neutral", "summary": "conclude hook unresolved", "blocked": False}
    env = dict(os.environ)
    env["BLOCKING"] = "1" if blocking else "0"
    # Generic capability (not protocol-specific): expose the state checkout so a
    # conclude hook can read any node's persisted evidence directly — e.g. read a
    # deeply-nested leg's gather evidence whose path the input-resolver cannot
    # reach from a sibling root child. "" when there is no checkout (degraded).
    env["CONCLUDE_STATE_DIR"] = dir_ or ""
    workdir = None
    declared = lib.state_inputs(proto, state_id)
    if dir_ is not None and declared:
        fo = lib._fanout_state(proto)
        phase = fo["id"] if (fo and lib.is_multiphase(proto)) else None
        consuming_branch = tree_path[-2] if tree_path and len(tree_path) >= 2 else None
        resolved = lib.resolve_inputs(
            proto,
            dir_,
            lib.protocol_id(proto_path),
            instance,
            consuming_branch=consuming_branch,
            consuming_phase=phase,
            inputs=declared,
            consuming_path=tree_path,
        )
        workdir = tempfile.mkdtemp(prefix="conclude-inputs-")
        lib.materialize_inputs(resolved, workdir)
        env["CONCLUDE_INPUTS_DIR"] = os.path.join(workdir, "inputs")
    try:
        result = subprocess.run([path, evid, instance], text=True,
                                stdout=subprocess.PIPE, env=env)
        try:
            parsed = json.loads(result.stdout.strip())
            if isinstance(parsed, dict) and "blocked" in parsed:
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return {"conclusion": "neutral", "summary": "conclude hook returned no verdict", "blocked": False}
    finally:
        # Materialized inputs are only needed for the hook subprocess above; remove the
        # temp dir so repeated conclude calls (triage/fix/mrp) don't leak it per run.
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def render_status_body(sf, headline, pid, instance, max_iter, github_repository):
    """Render the status-comment body as a projection of state.history.
    Byte-identical to the bash render_status_body function."""
    state_branch = os.environ.get("STATE_BRANCH", "agentic-state")
    link = f"https://github.com/{github_repository}/blob/{state_branch}/{pid}/{instance}.yaml"

    state_data = lib.load_yaml(sf)
    history = state_data.get("history", []) or []

    lines_list = []
    for entry in history:
        it = entry.get("iteration", "?")
        fb = entry.get("feedback", "") or ""
        if not fb:
            lines_list.append(f"- ✅ iteration {it}/{max_iter} — all checks passed")
        else:
            lines_list.append(f"- ✗ iteration {it}/{max_iter} — {fb}")
    lines = "\n".join(lines_list)

    return f"\U0001f50d **{pid} · {instance}**\n\n{lines}\n\n{headline}\n\n[Full state & audit trail]({link})\n"


def update_status_comment(sf, inf, branch, pr, pid, instance, proto_path, dir_,
                          headline, max_iter, github_repository):
    """Branch-aware status-comment writer.

    Multi-phase protocols carry ONE protocol-level comment keyed in
    _instance.yaml and rendered across every phase — so for them we ignore
    `branch`/`headline` (the renderer derives the headline from state) and key on
    `inf`. Single-phase fan-out keeps the per-fan-out comment; single-agent keeps
    its per-state-file comment. Both single-phase paths stay byte-identical."""
    with open(proto_path) as fh:
        proto = json.load(fh)
    if lib.is_multiphase(proto):
        if not os.path.isfile(inf):
            return
        body = lib.render_pipeline_status_body(dir_, pid, instance, proto_path)
        lib.upsert_status_comment(inf, pr, body)
        return
    if branch:
        # fan-out branch: shared comment keyed in _instance.yaml
        if not os.path.isfile(inf):
            return
        body = lib.render_fanout_status_body(dir_, pid, instance, proto_path)
        lib.upsert_status_comment(inf, pr, body)
    else:
        body = render_status_body(sf, headline, pid, instance, max_iter, github_repository)
        lib.upsert_status_comment(sf, pr, body)


def main():
    if len(sys.argv) != 6:
        sys.stderr.write(
            "usage: advance.py <state_workdir> <instance-key> <protocol.json> "
            "<verdicts.json> <evidence.json>\n"
        )
        sys.exit(1)

    dir_ = sys.argv[1]
    instance = sys.argv[2]
    proto_path = sys.argv[3]
    verdicts_path = sys.argv[4]
    evid = sys.argv[5]

    branch = os.environ.get("BRANCH", "")
    phase = os.environ.get("PHASE", "")
    substate = os.environ.get("SUBSTATE", "")
    # NODE_PATH (NOT PATH — the OS executable search path) is the dot-joined
    # canonical TREE path of the leg being advanced. When set it drives a
    # depth-N path-aware advance (the only way to express depth > 3). Empty →
    # the legacy BRANCH/PHASE/SUBSTATE coords (depth <=3, byte-identical).
    node_path_env = os.environ.get("NODE_PATH", "")
    pr = os.environ.get("PR", instance)
    agent_run_id = os.environ.get("AGENT_RUN_ID", "unknown")
    github_repository = os.environ.get("GITHUB_REPOSITORY", "")

    # Load protocol
    with open(proto_path) as f:
        proto = json.load(f)

    pid = lib.protocol_id(proto_path)

    import paths as _paths
    tree_path = None        # carried into advance_node only in NODE_PATH mode
    file_path = None        # file-naming path (state_path-converted)

    if not node_path_env:
        # NODE_PATH is the SOLE coordinate of the unified engine. Every advance
        # carries the canonical tree path of the leg being advanced; the legacy
        # BRANCH/PHASE/SUBSTATE derivation has been removed.
        sys.stderr.write("[advance] NODE_PATH is required\n")
        sys.exit(1)

    # ---- NODE_PATH (depth-N) coordinate derivation ----
    tree_path = node_path_env.split(".")
    try:
        _unit = lib.resolve_agent_unit_path(proto, tree_path)
    except ValueError as e:
        sys.stderr.write(f"[advance] {e}\n")
        sys.exit(1)
    agent_state = _unit["agent_state"]
    max_iter = _unit["max_iterations"]
    life_state = _unit["life_state"]
    # Surface branch/substate so the `if branch and substate:` sub-pipeline gates
    # in main() fire; these are the leg's immediate parent + own ids (advance_node
    # uses tree_path for real navigation).
    branch = tree_path[-2] if len(tree_path) >= 2 else ""
    substate = tree_path[-1]
    phase = ""
    file_path = lib.state_path(proto, tree_path)
    sf = lib.state_file(dir_, pid, instance, path=file_path)
    cr_name = pid + "/" + "/".join(tree_path[1:])

    # Checkout state
    lib.state_checkout(dir_)

    # Recover missing state file
    if not os.path.isfile(sf):
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        seed = {
            "protocol": pid,
            "instance": instance,
            "state": life_state,
            "iteration": 1,
            "gates": {},
            "history": [],
        }
        lib.dump_yaml(sf, seed)

    # Read current state
    state_data = lib.load_yaml(sf)
    iter_ = int(state_data.get("iteration", 1))
    max_iter = int(max_iter) if max_iter is not None else 3

    # Load verdicts
    with open(verdicts_path) as f:
        verdicts = json.load(f)

    results = verdicts.get("results", [])
    # DECIDE: the process axis (iterate/done/failed) is a pure fold over the
    # verdicts + their on_fail severities. `blocking` (a block-severity fail)
    # has no consumer in M1 — the M2 phase-gate will read it.
    process, blocking = lib.decide(results, iterations_remaining=(iter_ < max_iter))

    # Feedback fed back to the agent: only iterate-severity failures, since the
    # agent cannot fix advisory/block facts by re-running. Defaulting on_fail to
    # "iterate" keeps the single-agent regression path byte-identical (all v1
    # checks are iterate-severity, so this is every non-pass verdict).
    fb_parts = [r.get("feedback", "") for r in results
                if not r.get("pass", False) and r.get("on_fail", "iterate") == "iterate"]
    fb = "; ".join(p for p in fb_parts if p)
    if not fb and len(results) == 0:
        fb = "no check verdicts produced (checks job failure?)"

    # Checks map: {check: "pass"/"fail"}
    checks_map = {}
    for r in results:
        checks_map[r["check"]] = "pass" if r.get("pass", False) else "fail"

    # Append history entry
    history_entry = {
        "iteration": iter_,
        "agent_run_id": agent_run_id,
        "checks": checks_map,
        "feedback": fb,
    }
    state_data = lib.load_yaml(sf)
    if "history" not in state_data or state_data["history"] is None:
        state_data["history"] = []
    state_data["history"].append(history_entry)
    lib.dump_yaml(sf, state_data)

    sha = os.environ.get("PR_HEAD_SHA", "")
    inf = lib.instance_file(dir_, pid, instance)

    # Bundle the leg's stable identity so the depth-N walk helpers take one ctx
    # object. cursor_sf varies by call site (set per-arm below before advance_node).
    ctx = LegCtx(dir_=dir_, pid=pid, instance=instance, branch=branch, phase=phase,
                 substate=substate, sf=sf, cursor_sf="", inf=inf, pr=pr,
                 proto_path=proto_path, cr_name=cr_name, max_iter=max_iter,
                 github_repository=github_repository, sha=sha, life_state=life_state,
                 tree_path=tree_path, file_path=file_path, proto=proto)

    # Branch: mutate state → publish/side-effects → status-comment → cas_push → dispatch
    if process == "done":
        # Mark this phase/unit done.
        state_data = lib.load_yaml(sf)
        state_data["state"] = "done"
        lib.dump_yaml(sf, state_data)

        # Persist the evidence artifact so downstream `inputs` can resolve it.
        # Best-effort: a missing/empty evid file is silently skipped.
        persist_output(ctx, evid)

        # --- FLAT nested-fanout child leg (NODE_PATH, parent is a FANOUT). ---
        # Its parent is a fanout, NOT a sub-pipeline sequence, so there is no
        # leg-cursor to advance: the leg is its OWN terminal (tracked by the
        # fanout's per-leg files + __join.yaml). Mark this leg's own sf done and
        # fire the enclosing fanout's path-keyed join — DO NOT write a cursor
        # file at the parent (that would prematurely mark the whole fanout done).
        if _paths.is_fanout(proto, _paths.parent_path(tree_path)):
            lib.set_check_run(cr_name, sha, "completed", "success",
                              f"{substate} complete", "")
            update_status_comment(sf, inf, branch, pr, pid, instance, proto_path, dir_,
                                  "✅ done — published.", max_iter, github_repository)
            lib.cas_push(dir_, f"{instance}: {'.'.join(tree_path)} done → leg done")
            fire_join(pid, instance, branch, _join_path(proto, tree_path))
            return

        # --- Sub-pipeline branch leg: advance the BRANCH CURSOR, not the phase. ---
        if branch and substate:
            ctx.cursor_sf = lib.state_file(
                dir_, pid, instance,
                path=lib.state_path(proto, _paths.parent_path(tree_path)))
            advance_node(ctx, process="done")
            return

        # --- Depth-1 AGENT phase (root child) clear tail. ---
        # When the node is a root-level agent phase (e.g. code-review's
        # `preflight`), advance the root cursor via path-continue.
        if (_paths.is_root_child(proto, tree_path)
                and _paths.node_kind(proto, tree_path) == "agent"):
            _this_state = lib.state_by_id(proto, agent_state)
            _conclude = run_conclude_hook(
                proto_path, proto, agent_state, evid, instance, blocking,
                dir_=dir_, tree_path=tree_path)
            hook = run_publish_hook(proto_path, proto, branch, agent_state, evid, instance, pid)
            if _conclude is not None:
                concl = _conclude.get("conclusion", "neutral")
                csum = _conclude.get("summary", "")
            else:
                concl = hook.get("conclusion", "neutral")
                csum = hook.get("summary", "")
            _phase_id = tree_path[-1]
            if (_conclude is not None and _conclude.get("blocked")
                    and (_this_state or {}).get("on_blocked") == "halt"):
                # GATE BLOCKED → terminate the pipeline before the next phase.
                state_data = lib.load_yaml(sf)
                state_data["state"] = "failed"
                lib.dump_yaml(sf, state_data)
                lib.set_check_run(pid, sha, "completed", "failure", "Gate blocked",
                                  csum or "A required gate did not pass; pipeline halted.")
                lib.set_check_run(cr_name, sha, "completed", "failure", "Gate blocked", csum)
                inst_data = lib.load_yaml(inf) if os.path.isfile(inf) else {}
                inst_data["halted"] = {"phase": _phase_id, "reason": "blocked", "sha": sha}
                lib.dump_yaml(inf, inst_data)
                update_status_comment(
                    sf, inf, branch, pr, pid, instance, proto_path, dir_,
                    "⛔ blocked", max_iter, github_repository
                )
                notice = (f"⛔ **{_phase_id}** gate blocked: "
                          f"{csum or 'a required gate did not pass'}. "
                          f"A write-access user can comment `/override <reason>` "
                          f"to proceed past this gate.")
                lib.post_pr_comment(pr, notice)
                lib.ensure_phase_label(dir_, pid, instance, proto, pr, "blocked")
                lib.cas_push(dir_, f"{instance}: phase {_phase_id} blocked → pipeline halted")
            else:
                # GATE CLEAR → advance root cursor via path-continue.
                # `concl` is never "blocked" here: a blocked conclude goes to
                # the halt arm above; this arm only runs on a clear gate.
                nxt = _paths.next_sibling(proto, tree_path)
                lib.set_check_run(cr_name, sha, "completed", "success",
                                  "Gate complete", csum)
                inst = lib.load_yaml(inf) if os.path.isfile(inf) else {}
                if nxt:
                    inst["phase"] = nxt
                    lib.dump_yaml(inf, inst)
                    update_status_comment(
                        sf, inf, branch, pr, pid, instance, proto_path, dir_,
                        "⏳ advancing", max_iter, github_repository
                    )
                    lib.ensure_phase_label(dir_, pid, instance, proto, pr, nxt)
                    lib.cas_push(dir_, f"{instance}: phase {_phase_id} clear → advancing to {nxt}")
                    lib.dispatch_continue(pid, instance, path=nxt)
                else:
                    # No further sibling → pipeline complete.
                    lib.set_check_run(pid, sha, "completed", "success", "Complete", csum)
                    update_status_comment(
                        sf, inf, branch, pr, pid, instance, proto_path, dir_,
                        "✅ complete", max_iter, github_repository
                    )
                    lib.ensure_phase_label(dir_, pid, instance, proto, pr, "done")
                    lib.cas_push(dir_, f"{instance}: phase {_phase_id} clear → done (no further phase)")
            return

        # Remaining done case: a fan-out leg whose parent is the TOP fanout (the
        # flat_fanout_child block above handles nested fanouts; root-child agents,
        # sub-pipeline legs, and nested-fanout children all returned earlier). Run
        # publish for side-effects, mark the leg done, and fire the (path-less) top
        # join. Root-level agent phases advance via the NODE_PATH root-child block
        # above (path-continue).
        hook = run_publish_hook(proto_path, proto, branch, agent_state, evid, instance, pid)
        concl = hook.get("conclusion", "neutral")
        csum = hook.get("summary", "")
        lib.set_check_run(cr_name, sha, "completed", concl, "Review complete", csum)
        update_status_comment(
            sf, inf, branch, pr, pid, instance, proto_path, dir_,
            "✅ done — published.",
            max_iter, github_repository
        )
        lib.cas_push(dir_, f"{instance}: checks passed at iteration {iter_} → published, done")
        fire_join(pid, instance, branch)

    elif process == "iterate":
        next_iter = iter_ + 1
        state_data = lib.load_yaml(sf)
        state_data["iteration"] = next_iter
        lib.dump_yaml(sf, state_data)

        lib.set_check_run(
            cr_name, sha, "in_progress", "",
            "Review in progress",
            f"Iteration {iter_} failed checks; retrying as iteration {next_iter}/{max_iter}."
        )
        update_status_comment(
            sf, inf, branch, pr, pid, instance, proto_path, dir_,
            f"⏳ iteration {iter_} failed checks — retrying as iteration {next_iter}/{max_iter}…",
            max_iter, github_repository
        )
        lib.cas_push(dir_, f"{instance}: iteration {iter_} failed checks → iteration {next_iter}")

        # Re-dispatch carrying the full tree path so the re-dispatched continue
        # resumes the same depth-N leg (next.py reads NODE_PATH). branch/substate
        # ride along for the depth-<=3 GHA relay; they are derived from the tree path.
        gh_api(
            f"repos/{github_repository}/dispatches",
            "-f", "event_type=protocol-continue",
            "-F", f"client_payload[protocol]={pid}",
            "-F", f"client_payload[instance]={instance}",
            "-F", f"client_payload[branch]={branch}",
            "-F", f"client_payload[substate]={substate}",
            "-F", f"client_payload[path]={'.'.join(tree_path)}",
        )

    else:  # process == "failed"
        # Exhausted
        state_data = lib.load_yaml(sf)
        state_data["state"] = "failed"
        lib.dump_yaml(sf, state_data)

        # A FLAT nested-fanout child leg (parent is a FANOUT) is its OWN terminal:
        # sf is already marked failed above; there is no leg-cursor to advance, so
        # we must NOT write the parent fanout file. Only a sub-pipeline SEQUENCE
        # leg has a cursor (advance_node marks the branch file failed).
        flat_fanout_child = _paths.is_fanout(proto, _paths.parent_path(tree_path))
        if branch and substate and not flat_fanout_child:
            ctx.cursor_sf = lib.state_file(
                dir_, pid, instance,
                path=lib.state_path(proto, _paths.parent_path(tree_path)))
            advance_node(ctx, process="failed")

        # A root-level agent phase that exhausts its iterations is a terminal phase
        # failure (label it). A fan-out leg reaching here is NOT a phase terminal —
        # join.py (fan-out) owns that.
        if (_paths.is_root_child(proto, tree_path)
                and _paths.node_kind(proto, tree_path) == "agent"):
            lib.ensure_phase_label(dir_, pid, instance, proto, pr, "failed")

        lib.set_check_run(
            cr_name, sha, "completed", "failure",
            "Review failed",
            f"Could not produce a valid review after {max_iter} iterations."
        )
        update_status_comment(
            sf, inf, branch, pr, pid, instance, proto_path, dir_,
            f"❌ **failed** after {max_iter} iterations.",
            max_iter, github_repository
        )
        lib.cas_push(dir_, f"{instance}: iterations exhausted → failed")
        # A NESTED failed leg fires its enclosing fanout's path-keyed join; the TOP
        # fanout (or legacy depth-<=3) fires a path-less join (byte-identical).
        fire_join(pid, instance, branch, _join_path(proto, tree_path))


if __name__ == "__main__":
    main()

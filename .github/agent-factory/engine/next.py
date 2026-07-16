#!/usr/bin/env python3
# next.py <state_workdir> <instance-key> <protocol.json> <command> [head_sha]
# Pure planner: reads (state, protocol, command), emits an action JSON on stdout.
# The WORKFLOW decides what an event means and passes a command; the planner never
# sniffs events. Commands:
#   start / reset   enter the protocol from its first top-level node via enter_root
#                   (start/reset both seed a fresh run; reset is invoked when a new
#                   head commit invalidates the old run).
#   continue        resume the leg named by NODE_PATH (the SOLE coordinate of the
#                   unified engine) — seed/dispatch the fanout/agent/gate/merge it
#                   resolves to. A continue WITHOUT a resolvable NODE_PATH errors.
#   answer / override / resolve-gate   human-gate commands (path-aware).
# head_sha (optional) is recorded as instance metadata (the check-run target); it is
# NEVER compared to decide policy — that decision lives in the workflow.
import json
import os
import re
import sys

# The script's directory is sys.path[0], so `import lib` finds lib.py alongside.
import lib
import paths

DIR = sys.argv[1]
INSTANCE = sys.argv[2]
PROTO = sys.argv[3]
COMMAND = sys.argv[4]
HEAD_SHA = sys.argv[5] if len(sys.argv) > 5 else ""
# NODE_PATH (NOT PATH — that is the OS executable search path) is the dot-joined
# tree-navigation path of a `continue` dispatch. It is the SOLE coordinate of the
# unified engine: when it resolves to a fanout node the planner emits that fanout's
# children matrix (a nested fanout is dispatched as its own engine invocation), to
# an agent it seeds + emits run-agent, to a gate it opens the gate, to a merge it
# runs the reduce hook. start/reset ignore it (they route to enter_root).
NODE_PATH = os.environ.get("NODE_PATH", "")

with open(PROTO) as f:
    proto_data = json.load(f)

PID = proto_data["name"]  # equivalent to lib.protocol_id(PROTO); proto_data already loaded

try:
    lib.check_depth(proto_data)
except ValueError as _e:
    sys.stderr.write(f"[next] {_e}\n")
    sys.exit(2)

try:
    lib.validate_protocol(proto_data)
except ValueError as _e:
    sys.stderr.write(f"[next] {_e}\n")
    sys.exit(2)

# Check out the state branch first: both the fan-out planner (below) and the
# single-agent path write into DIR, and state_checkout only depends on DIR,
# so doing it here is behaviour-preserving for the single-agent path.
lib.state_checkout(DIR)


def _fanout_action(proto, path, branches):
    """Build the run-fanout action dict for the fanout at `path`. Single-phase
    keeps reason='fanout' with NO phase key; multi-phase uses reason='phase:<id>'
    and adds the phase key. `branches` stays the authoritative key the GHA layer
    reads; `legs` is emitted alongside as the path-aware companion for
    nested-fanout matrix wiring."""
    multi = lib.is_multiphase(proto)
    act = {"action": "run-fanout", "iteration": 1, "feedback": "",
           "reason": (f"phase:{path[-1]}" if multi else "fanout")}
    if multi:
        act["phase"] = path[-1]
    act["branches"] = branches
    # `legs` is the path-aware companion to `branches` (Stage 3/4b): one entry per
    # child carrying its full LEAF tree path + agent workflow. Additive —
    # `branches` stays authoritative for the depth-<=3 GHA layer; `legs` is the
    # single uniform shape the GHA matrix reads for node path + workflow.
    # Leaf path = fanout_path + branch_id for a FLAT branch;
    #            fanout_path + branch_id + first_substate for a SUB-PIPELINE branch
    # (`branches[]` dicts from _seed_child already carry `substate` for sub-pipelines).
    legs = []
    for b in branches:
        leaf = path + [b["id"]] + ([b["substate"]] if b.get("substate") else [])
        leg = {"path": ".".join(leaf), "workflow": b.get("workflow")}
        if b.get("inputs"):            # dynamic legs only; static branches never carry this
            leg["inputs"] = b["inputs"]
        legs.append(leg)
    act["legs"] = legs
    lib.check_matrix_size(legs)
    return act


def enter_node(proto, path, command, emit=True):
    """Recursive sequencer: seed the node at the tree-navigation `path` and, when
    `emit`, print its action JSON (run-agent / run-fanout / gate-open noop).

    The recursive sequencer for the unified engine: enter_root and the NODE_PATH
    `continue` arms call it. INSTANCE-file / phase-label / cas_push side-effects
    stay with those callers — this function only seeds the node's own state
    file(s) and emits. Every file call routes the tree path through lib.state_path
    (single-phase drops the leading top fanout id), so depth-<=3 files keep their
    historical layout.

    `path` is rooted at the top phase/fanout id; e.g. the top fanout enters as
    [fanout_id]. `command` is carried for parity with the recursive callers."""
    kind = paths.node_kind(proto, path)
    node = paths.node_at_path(proto, path)
    life = paths.enclosing_fanout_id(proto, path)
    fpath = lib.state_path(proto, path)
    if kind == "sequence":
        first = paths.first_child_id(node)
        cf = lib.state_file(DIR, PID, INSTANCE, path=fpath)
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        lib.dump_yaml(cf, {"protocol": PID, "instance": INSTANCE, "state": life,
                           "sub_state": first, "iteration": 1, "gates": {}, "history": []})
        return enter_node(proto, path + [first], command, emit=emit)
    if kind == "fanout":
        # Reset the join barrier on ENTERING this fanout so its own join can fire.
        # NESTED fanouts (len > 1) use a path-keyed __join.yaml marker. A TOP-level
        # fanout (len 1) uses the instance-wide _instance.yaml `joined` flag — which a
        # PRIOR top-level fanout (e.g. `review` before `post-fix`) leaves latched True;
        # without this reset join.py would no-op the second fanout's barrier and the
        # pipeline would stall (the next phase, e.g. mrp, never dispatched). Idempotent
        # for the first fanout (joined already absent/False). The change is staged with
        # the seeded legs and CAS-pushed by the fanout-entry caller.
        if len(path) > 1:
            lib.write_join(DIR, PID, INSTANCE, lib.state_path(proto, path), {"joined": False})
        else:
            inf = lib.instance_file(DIR, PID, INSTANCE)
            if os.path.isfile(inf):
                _inst = lib.load_yaml(inf)
                if _inst.get("joined"):
                    _inst["joined"] = False
                    lib.dump_yaml(inf, _inst)
        if node.get("expand"):
            # --- DYNAMIC fanout: materialize legs from the expander manifest. ---
            each = node.get("each", {})
            items = lib.run_expander(DIR, PID, INSTANCE, PROTO, node)   # fail-loud on hook error
            manifest = lib.build_manifest(items, node["expand"]["id_from"],
                                          node["expand"]["max_legs"])    # fail-loud on over-cap/dupe
            lib.write_manifest(DIR, PID, INSTANCE, path, manifest)
            branches = []
            for leg in manifest["legs"]:
                cfg = dict(each)
                cfg["id"] = leg["id"]
                seeded = _seed_child(proto, path + [leg["id"]], cfg)
                lib.stage_item(DIR, PID, INSTANCE, lib.state_path(proto, path + [leg["id"]]),
                               node["expand"]["as"], leg["item"])
                seeded["inputs"] = {node["expand"]["as"]:
                                    lib.project_matrix_item(leg["item"], node["expand"].get("matrix_fields"))}
                branches.append(seeded)
            # zero legs → branches == [] falls through the shared tail unchanged (vacuous fanout)
        else:
            branches = [_seed_child(proto, path + [b["id"]], b) for b in node.get("branches", [])]
        if emit:
            print(json.dumps(_fanout_action(proto, path, branches)))
            return None
        # emit=False → return the branch emit-dicts so the caller can print the
        # run-fanout AFTER its own instance-file / label / cas_push side-effects
        # (preserving the legacy seed→side-effects→cas_push→emit ordering).
        return branches
    if kind == "agent":
        sf = lib.state_file(DIR, PID, INSTANCE, path=fpath)
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        node_state = life or path[-1]
        # Preserve an in-flight iterate. An iterate re-dispatch is a `continue` onto
        # the SAME agent phase that advance already advanced (iteration N + a history
        # entry carrying the failed round's feedback). Re-seeding it to
        # iteration:1/history:[] would reset the bounded iterate loop into an INFINITE
        # one (the counter never reaches max_iterations) and drop the feedback the
        # agent needs to converge. So only a FRESH entry (no state file, or a prior
        # terminal, or a non-continue command) seeds; an in-flight continue preserves.
        existing = lib.load_yaml(sf) if os.path.exists(sf) else None
        preserve = bool(command == "continue" and existing
                        and existing.get("state") == node_state)
        if preserve:
            st = existing
        else:
            st = {"protocol": PID, "instance": INSTANCE, "state": node_state,
                  "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": []}
            lib.dump_yaml(sf, st)
        it = int(st.get("iteration", 1))
        _hist = st.get("history") or []
        fb = _hist[-1].get("feedback", "") if _hist else ""
        if emit:
            act = {"action": "run-agent", "iteration": it, "feedback": fb,
                   "reason": f"phase:{path[-1]}", "path": ".".join(path),
                   "workflow": paths.node_at_path(proto, path).get("workflow")}
            if lib.is_multiphase(proto):
                act["phase"] = path[-1]
            print(json.dumps(act))
        # `seeded` tells a `continue` caller whether new state was written (so it must
        # cas_push it) or an in-flight iterate was preserved (nothing new → an empty
        # cas_push would fail loudly).
        return {"id": path[-1], "workflow": node.get("workflow"),
                "iteration": it, "feedback": fb, "seeded": not preserve}
    if kind == "gate":
        pr = lib.pr_from_instance(INSTANCE)
        lib.open_gate(DIR, PID, INSTANCE, PROTO, path[-1], HEAD_SHA, pr,
                      phase=(path[-1] if lib.is_multiphase(proto) else None))
        if emit:
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"gate-open:{path[-1]}"}))
        return None
    return None


def _seed_child(proto, path, cfg):
    """Seed one fan-out child (flat agent OR sub-pipeline) WITHOUT emitting; return
    its run-fanout branch dict (carrying `substate` for a sub-pipeline). The dict
    field-orderings and the per-file head_sha rule reproduce the legacy seed_branch
    output byte-for-byte for depth-<=3. All file paths route through lib.state_path."""
    life = paths.enclosing_fanout_id(proto, path)
    if paths.is_sequence(proto, path):
        first = paths.first_child_id(cfg)
        cf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, path))
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        lib.dump_yaml(cf, {"protocol": PID, "instance": INSTANCE, "state": life,
                           "sub_state": first, "iteration": 1, "gates": {}, "history": []})
        sf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, path + [first]))
        lib.dump_yaml(sf, {"protocol": PID, "instance": INSTANCE, "state": life,
                           "iteration": 1, "gates": {}, "head_sha": HEAD_SHA, "history": []})
        fc = paths.node_at_path(proto, path + [first])
        return {"id": path[-1], "workflow": fc.get("workflow"),
                "substate": first, "iteration": 1, "feedback": ""}
    sf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, path))
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    flat = {"protocol": PID, "instance": INSTANCE, "state": life,
            "iteration": 1, "gates": {}, "history": []}
    if lib.is_multiphase(proto):
        flat["head_sha"] = HEAD_SHA
    lib.dump_yaml(sf, flat)
    return {"id": path[-1], "workflow": cfg.get("workflow"), "iteration": 1, "feedback": ""}


def _reset_wipe(inf, inst_dir, prev, pr):
    """Wipe all prior-run state files for this instance and finalize any
    superseded status comment. Called on `start`/`reset` entry (via enter_root).
    A fresh run with no prior files is safe (no-op when inst_dir is empty or
    doesn't exist yet)."""
    # Abandon the prior run's status comment so this run gets a FRESH one.
    # Render its final state FIRST (the files still exist), edit the old
    # comment once with a "superseded" banner above that frozen snapshot,
    # then drop the id — ensure_status_comment creates the new comment.
    old_cid = prev.get("status_comment_id")
    if old_cid:
        frozen = lib.render_instance_status_body(DIR, PID, INSTANCE, PROTO)
        banner = (f"↻ _Superseded — a newer run started (new commit or "
                  f"`/review`); see the newest **{PID} · {INSTANCE}** comment below._")
        lib.finalize_superseded_comment(pr, old_cid, f"{banner}\n\n{frozen}")
    # Remove the prior run's phase label so a restart from e.g. "approval
    # gate" does not orphan it (the wipe below drops our tracking of it).
    lib.remove_pr_label(pr, prev.get("phase_label", ""))
    # Wipe every prior-run state file (phase yamls + fan-out legs + the old
    # _instance.yaml); cas_push stages the deletions. Start the instance clean.
    if os.path.isdir(inst_dir):
        for name in os.listdir(inst_dir):
            p = os.path.join(inst_dir, name)
            if os.path.isfile(p):
                os.remove(p)


def _emit_for_node(path, branches):
    """Emit the action JSON for the node at `path`. `branches` is the return
    value from enter_node (emit=False) — the branch emit-dicts for fanout nodes,
    None for agent/gate."""
    kind = paths.node_kind(proto_data, path)
    if kind == "fanout":
        print(json.dumps(_fanout_action(proto_data, path, branches)))
        return
    if kind == "gate":
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate-open:{path[-1]}"}))
        return
    # agent (or sequence that enters down to an agent leaf via enter_node)
    node = paths.node_at_path(proto_data, path)
    act = {"action": "run-agent", "iteration": 1, "feedback": "",
           "reason": f"phase:{path[-1]}",
           "path": ".".join(path),
           "workflow": node.get("workflow")}
    if lib.is_multiphase(proto_data):
        act["phase"] = path[-1]
    print(json.dumps(act))


def enter_root(command, head_sha):
    """Unified entry for start/reset: seed the FIRST top-level node via the
    recursive sequencer, create _instance.yaml, apply labels, CAS-push, and emit
    the node's action. The single entry point for EVERY protocol shape
    (single-agent, single-phase fanout, multi-phase)."""
    first = paths.root_ids(proto_data)[0]
    pr = lib.pr_from_instance(INSTANCE)
    inf = lib.instance_file(DIR, PID, INSTANCE)
    inst_dir = os.path.dirname(inf)
    os.makedirs(inst_dir, exist_ok=True)
    prev = lib.load_yaml(inf) if os.path.isfile(inf) else {}
    _reset_wipe(inf, inst_dir, prev, pr)
    lib.apply_setup_label(proto_data, pr)
    lib.dump_yaml(inf, {"protocol": PID, "instance": INSTANCE,
                        "head_sha": head_sha, "phase": first, "joined": False})
    lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, first)
    branches = enter_node(proto_data, [first], command, emit=False)
    lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter root phase {first} ({command})")
    _emit_for_node([first], branches)


def do_override():
    """HITL escape-hatch: a write-access human forces a *blocked* gate to advance
    one phase. Authorization happened in the workflow (ctx step); next.py only ever
    sees an authorized override. Reads the `halted` marker on _instance.yaml. On a
    valid blocked marker, records the override beside the failure, clears the
    marker, and seeds+dispatches the next phase. Otherwise posts an explanatory
    comment and halts — no state change. emit_halt is defined below this point in
    the script, so the halt JSON is printed inline here."""
    pr = lib.pr_from_instance(INSTANCE)
    inf = lib.instance_file(DIR, PID, INSTANCE)

    def refuse(message, reason):
        lib.post_pr_comment(pr, message)
        print(json.dumps({"action": "halt", "iteration": 0, "feedback": "", "reason": reason}))

    if not os.path.isfile(inf):
        refuse(f"Nothing to override — no {PID} run exists for this PR.",
               "override: no instance")
        return

    inst = lib.load_yaml(inf)
    halted = inst.get("halted") or {}

    if halted.get("reason") == "blocked":
        blocked_phase = halted.get("phase")
        nxt = paths.next_sibling(proto_data, [blocked_phase])
        if not nxt:
            refuse("The blocked gate is the final phase; there is nothing to advance to.",
                   "override: no next phase")
            return
        actor = os.environ.get("OVERRIDE_ACTOR", "")
        reason = os.environ.get("OVERRIDE_REASON", "")
        inst.setdefault("overrides", []).append(
            {"phase": blocked_phase, "actor": actor, "reason": reason})
        inst.pop("halted", None)
        # Advance the root cursor to `nxt` and dispatch a path-continue; the
        # continue dispatch will seed+enter the next phase via the NODE_PATH guard.
        # Note: _instance.yaml's head_sha stays the instance-seed head (as before —
        # the authoritative head is recorded per-phase in each phase's own state file).
        inst["phase"] = nxt
        lib.dump_yaml(inf, inst)
        note = f"⚠️ {blocked_phase} gate was blocked — overridden by @{actor}; proceeding to {nxt}."
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, nxt)
        lib.cas_push(DIR, f"{INSTANCE}: gate {blocked_phase} overridden by {actor} → continue {nxt}")
        lib.dispatch_continue(PID, INSTANCE, path=nxt)
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"override:continue:{nxt}"}))
        return

    # Not a blocked halt → give a precise message: exhausted vs simply not-halted.
    cursor = inst.get("phase") or ""
    cursor_sf = lib.state_file(DIR, PID, INSTANCE, phase=cursor) if cursor else ""
    cursor_state = (lib.load_yaml(cursor_sf).get("state")
                    if cursor_sf and os.path.isfile(cursor_sf) else "")
    if cursor_state == "failed":
        refuse(f"The {cursor} gate is exhausted (it could not produce a valid result), "
               f"not blocked. Override only applies to a gate that ran and returned a "
               f"blocking verdict; re-run the pipeline instead.",
               "override: exhausted")
    else:
        refuse("Nothing to override — the pipeline is not currently halted at a "
               f"blocked gate (current phase: {cursor}).",
               "override: not halted")


def do_resolve_gate():
    """Human approval gate resolution. write/admin auth happened in the workflow;
    next.py sees only an authorized actor. Reads GATE_DECISION/ACTOR/REASON/PR_AUTHOR
    from env, mutates the cursor gate's `gates` record, and advances (approve) or
    halts (request-changes / reject). Guards refuse with one PR comment + a halt
    action — no state change. A gate is 'live' when gates.state in {open,
    changes_requested}."""
    pr = lib.pr_from_instance(INSTANCE)
    inf = lib.instance_file(DIR, PID, INSTANCE)
    decision = os.environ.get("GATE_DECISION", "")
    actor = os.environ.get("GATE_ACTOR", "")
    reason = os.environ.get("GATE_REASON", "")
    pr_author = os.environ.get("GATE_PR_AUTHOR", "")

    def refuse(message, code):
        lib.post_pr_comment(pr, message)
        print(json.dumps({"action": "halt", "iteration": 0, "feedback": "", "reason": code}))

    if not os.path.isfile(inf):
        refuse(f"Nothing to resolve — no {PID} run exists for this PR.", "gate: no instance")
        return
    inst = lib.load_yaml(inf)
    cursor = inst.get("phase") or ""
    cur_state = lib.state_by_id(proto_data, cursor)
    if not cursor or not cur_state or cur_state.get("kind") != "gate":
        refuse(f"Nothing to resolve — no approval gate is currently open for this PR "
               f"(current phase: {cursor or 'none'}).", "gate: not a gate")
        return

    sf = lib.state_file(DIR, PID, INSTANCE, phase=cursor)
    gdata = lib.load_yaml(sf) if os.path.isfile(sf) else {}
    g = gdata.get("gates") or {}
    gstate = g.get("state", "")
    sha = gdata.get("head_sha", "") or HEAD_SHA
    cr_name = f"{PID}/{cursor}"

    if gstate == "rejected":
        refuse("This gate was rejected; push a new commit or comment `/review` to "
               "restart the pipeline.", "gate: rejected")
        return
    if gstate not in ("open", "changes_requested"):
        refuse(f"Nothing to resolve — the {cursor} gate is not awaiting a decision "
               f"(state: {gstate or 'unknown'}).", "gate: not live")
        return
    if (decision == "approve" and cur_state.get("approve_excludes_author")
            and actor and actor == pr_author):
        refuse(f"@{actor} the PR author cannot approve their own gate; another "
               f"write-access reviewer must `/approve`.", "gate: self-approve")
        return

    g.setdefault("history", []).append({"decision": decision, "actor": actor, "reason": reason})

    if decision == "approve":
        g["state"] = "approved"
        gdata["gates"] = g
        lib.dump_yaml(sf, gdata)
        lib.set_check_run(cr_name, sha, "completed", "success", "Approved", f"Approved by @{actor}.")
        nxt = paths.next_sibling(proto_data, [cursor])
        if nxt:
            note = f"✅ {cursor} gate approved by @{actor}; proceeding to {nxt}."
            if reason:
                note += f"\n\n> {reason}"
            lib.post_pr_comment(pr, note)
            # Advance the root cursor to `nxt` and dispatch a path-continue; the
            # continue dispatch seeds+enters the next phase (fan-out, agent, or gate)
            # via the NODE_PATH guard in next.py — path-based like the rest of the
            # unified engine.
            inst = lib.load_yaml(inf)
            inst["phase"] = nxt
            lib.dump_yaml(inf, inst)
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, nxt)
            lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} approved by {actor} → continue {nxt}")
            lib.dispatch_continue(PID, INSTANCE, path=nxt)
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"gate:approved:{cursor}:continue:{nxt}"}))
        else:
            lib.set_check_run(PID, sha, "completed", "success", "Complete", f"Approved by @{actor}.")
            note = f"✅ {cursor} gate approved by @{actor}; pipeline complete."
            if reason:
                note += f"\n\n> {reason}"
            lib.post_pr_comment(pr, note)
            body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
            lib.upsert_status_comment(inf, pr, body)
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "done")
            lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} approved by {actor} → done")
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"gate:approved:{cursor}"}))
        return

    if decision == "request-changes":
        g["state"] = "changes_requested"
        gdata["gates"] = g
        lib.dump_yaml(sf, gdata)
        lib.set_check_run(cr_name, sha, "completed", "failure", "Changes requested",
                          f"Changes requested by @{actor}.")
        body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
        lib.upsert_status_comment(inf, pr, body)
        note = (f"🔁 {cursor} gate — changes requested by @{actor}. Push a new commit to "
                f"re-run the pipeline, or a reviewer can `/approve`.")
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} changes requested by {actor}")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate:changes:{cursor}"}))
        return

    if decision == "reject":
        g["state"] = "rejected"
        gdata["gates"] = g
        gdata["state"] = "failed"
        lib.dump_yaml(sf, gdata)
        lib.set_check_run(cr_name, sha, "completed", "failure", "Rejected", f"Rejected by @{actor}.")
        lib.set_check_run(PID, sha, "completed", "failure", "Pipeline rejected", f"Rejected by @{actor}.")
        body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
        lib.upsert_status_comment(inf, pr, body)
        note = f"⛔ {cursor} gate rejected by @{actor}. Push a new commit or `/review` to restart."
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "failed")
        lib.cas_push(DIR, f"{INSTANCE}: gate {cursor} rejected by {actor} → failed")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate:rejected:{cursor}"}))
        return

    refuse(f"Unknown gate decision '{decision}'.", "gate: unknown decision")


def _find_open_gate(proto, want=""):
    """Return the full tree-navigation path to the first open data-gate, or None.
    Follows LIVE cursors recursively: at each fanout branch, read its cursor
    `sub_state`; if it is a gate in state 'open' return its path; if it is a
    nested fanout, descend into that fanout's child-branch cursors. First open
    gate wins (at most one gate per branch lineage is open at a time). `want`
    restricts the TOP-level branch only. For a depth-3 gate the returned path is
    byte-identical to the old (branch_id, gate_id) pair: [fanout_id, branch_id, gate_id].

    Multi-phase cursor awareness (I1 fix): for multi-phase protocols, resolve the
    fanout to scan from the _instance.yaml cursor phase, not the first fanout in
    the states list. `lib._fanout_state` always returns the FIRST fanout; in a
    protocol where the cursor is on a LATER fanout phase, that would scan the
    wrong branches and find nothing. Mirrors the pattern in join.py main()."""
    fo = None
    if lib.is_multiphase(proto):
        inf = lib.instance_file(DIR, PID, INSTANCE)
        if os.path.isfile(inf):
            cursor_phase = lib.load_yaml(inf).get("phase", "") or ""
            if cursor_phase:
                st = lib.state_by_id(proto, cursor_phase)
                if st and st.get("kind") == "fanout":
                    fo = st
    if fo is None:
        fo = lib._fanout_state(proto)
    if not fo:
        return None
    return _scan_fanout_for_open_gate(proto, [fo["id"]], fo, want, top=True)


def _scan_fanout_for_open_gate(proto, fanout_path, fo_node, want, top):
    for b in fo_node.get("branches", []):
        bid = b["id"]
        if top and want and bid != want:
            continue
        branch_path = fanout_path + [bid]
        cf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, branch_path))
        if not os.path.isfile(cf):
            continue
        sub = lib.load_yaml(cf).get("sub_state", "")
        if not sub:
            continue  # flat leg (no sub_state) or not yet started
        sub_path = branch_path + [sub]
        kind = paths.node_kind(proto, sub_path)
        if kind == "gate":
            gsf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, sub_path))
            if os.path.isfile(gsf) and \
                    lib.load_yaml(gsf).get("gates", {}).get("state") == "open":
                return sub_path
        elif kind == "fanout":
            res = _scan_fanout_for_open_gate(
                proto, sub_path, paths.node_at_path(proto, sub_path), want, top=False)
            if res:
                return res
    return None



def _parse_answers(body, prefix="/answer"):
    """Parse `<prefix> qID: value` pairs (one or many lines). Returns {id: value}.
    `prefix` is the protocol-configured comment prefix for the answer command
    (defaults to /answer). The body is UNTRUSTED input: it is parsed and stored
    in a JSON file whose path (never its content) is passed to the coverage
    check — safe."""
    out = {}
    for line in body.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            line = line[len(prefix):].strip()
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*[:=]\s*(.+)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def do_answer():
    """Parse answer comments, accumulate answers, run coverage check, advance gate.
    The comment prefix is the one the triggering protocol declared for the
    `answer` command (falls back to /answer) — never a protocol-coupled literal."""
    import subprocess as _sp
    pr = lib.pr_from_instance(INSTANCE)
    body = os.environ.get("ANSWER_BODY", "")
    actor = os.environ.get("ANSWER_ACTOR", "")
    prefix = lib.command_prefix(proto_data, "answer", "/answer")
    # Optional explicit branch: `<prefix> <branch> qID: val` — first bare token.
    want = ""
    head = body[len(prefix):].strip() if body.startswith(prefix) else body
    first = head.split()[0] if head.split() else ""
    if first and ":" not in first and "=" not in first:
        want = first

    gate_path = _find_open_gate(proto_data, want)
    if gate_path is None:
        lib.post_pr_comment(pr, "No open question gate to answer right now.")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": "answer: no open gate"}))
        return

    # Derive coords from the gate tree path via path helpers.
    # branch_path is the cursor file's tree path (parent of the gate leaf).
    branch = gate_path[-2]
    gate = gate_path[-1]
    branch_path = gate_path[:-1]
    # life is the leg's in-flight state value: the enclosing fanout id.
    # enclosing_fanout_id(["review","B","clarify"]) == "review".
    life = paths.enclosing_fanout_id(proto_data, gate_path)

    # File paths all derived from the gate/branch tree paths via lib.state_path so
    # depth-<=3 filenames stay byte-identical (single-phase drops the leading fanout id).
    gsf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto_data, gate_path))
    gdata = lib.load_yaml(gsf)
    questions = gdata.get("gates", {}).get("questions", []) or []
    # Interactive (issue-channel) gate: feedback goes to the question issue, not a PR.
    # `fb` is the comment target (the gate's issue if present, else the PR).
    issue_no = gdata.get("gates", {}).get("issue")
    fb = issue_no or pr

    # Merge new answers into the persisted answers artifact.
    apath = lib.output_artifact_path(DIR, PID, INSTANCE,
                                     path=lib.state_path(proto_data, gate_path), kind="answers")
    existing = {}
    if os.path.isfile(apath):
        try:
            existing = json.load(open(apath)).get("answers", {}) or {}
        except (json.JSONDecodeError, ValueError):
            existing = {}
    existing.update(_parse_answers(body, prefix))
    doc = {"questions": questions, "answers": existing}
    os.makedirs(os.path.dirname(apath), exist_ok=True)
    with open(apath, "w") as fh:
        json.dump(doc, fh)

    # Run the gate's answers-coverage check over the synthesized doc.
    # The check receives FILE PATHS, not answer content — no injection risk.
    # Path-aware (works at any depth): node_at_path resolves the gate node
    # directly. For a depth-3 gate this is the same dict branch_substates returned.
    gate_cfg = paths.node_at_path(proto_data, gate_path) or {}
    check_run = (gate_cfg.get("checks", [{}])[0]).get("run", "answers-coverage")
    pdir = os.path.dirname(os.path.abspath(PROTO))
    res = lib.resolve_executable(f"{pdir}/checks", check_run, pdir, "")
    kind, path = res.split("\t", 1)
    import tempfile
    empty_fd, empty = tempfile.mkstemp(prefix="answers-empty-")
    os.close(empty_fd)
    cov = _sp.run([path, apath, empty, empty], text=True, capture_output=True)
    verdict = json.loads(cov.stdout) if cov.stdout.strip() else {"pass": False, "feedback": "no verdict"}

    gdata["gates"].setdefault("history", []).append(
        {"actor": actor, "answers": list(_parse_answers(body, prefix).keys())})
    if not verdict.get("pass"):
        lib.dump_yaml(gsf, gdata)
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} gate {gate} partial answers")
        lib.post_pr_comment(fb, f"Recorded. Still needed: {verdict.get('feedback', '')}.")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": "answer: partial"}))
        return

    # Full coverage → close the gate, advance the branch cursor to the next sub-state.
    gdata["gates"]["state"] = "answered"
    lib.dump_yaml(gsf, gdata)

    # A NESTED gate (enclosing fanout is not the top one) advances the enclosing
    # sub-pipeline cursor and re-dispatches protocol-continue carrying the path —
    # next.py's continue-at-NODE_PATH guard then seeds/opens/dispatches the next
    # sibling by kind. The TOP-gate path below stays byte-identical (depth-3).
    fanout_path = paths.enclosing_fanout_path(proto_data, gate_path) or []
    if len(fanout_path) > 1:
        seq_path = paths.parent_path(gate_path)         # enclosing sequence cursor
        nxt = paths.next_sibling(proto_data, gate_path)
        sha = gdata.get("head_sha", "") or HEAD_SHA
        cf = lib.state_file(DIR, PID, INSTANCE,
                            path=lib.state_path(proto_data, seq_path))
        cur = lib.load_yaml(cf)
        lib.set_check_run(f"{PID}/{branch}/{gate}", sha, "completed", "success",
                          "Answered", f"Answered by @{actor}.")
        if nxt:
            cur["sub_state"] = nxt
            cur["state"] = life                          # leg stays in flight
            lib.dump_yaml(cf, cur)
            lib.cas_push(DIR, f"{INSTANCE}: gate {'.'.join(gate_path)} answered -> {nxt}")
            lib.post_pr_comment(pr, f"{gate} answered by @{actor}; continuing to {nxt}.")
            lib.dispatch_continue(PID, INSTANCE, path=".".join(seq_path + [nxt]))
        else:
            cur["state"] = "done"                        # gate was the last sub-state
            lib.dump_yaml(cf, cur)
            lib.cas_push(DIR, f"{INSTANCE}: gate {'.'.join(gate_path)} answered -> leg done")
            lib.fire_join_dispatch(PID, INSTANCE, fanout_path=".".join(fanout_path))
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": "answer: complete (nested)"}))
        return

    # Use path.next_sibling directly from gate_path so the correct enclosing
    # sequence is used regardless of which fanout phase the gate lives in.
    # lib.next_substate_id calls _fanout_state (first fanout) — in a multi-phase
    # protocol with the gate in a NON-first fanout phase it would pick the wrong
    # fanout and fail to find the sibling. (I1 fix — top-level advance tail.)
    nxt_sub = paths.next_sibling(proto_data, gate_path)
    cf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto_data, branch_path))
    cur = lib.load_yaml(cf)
    sha = gdata.get("head_sha", "") or HEAD_SHA
    if nxt_sub:
        nxt_path = branch_path + [nxt_sub]
        cur["sub_state"] = nxt_sub
        cur["state"] = life
        lib.dump_yaml(cf, cur)
        # Advance the cursor ONLY — do NOT pre-seed the next sub-state's file here.
        # The dispatched `continue` (continue-at-NODE_PATH agent arm) seeds it; if we
        # also seeded it, that arm would write identical content and its cas_push would
        # refuse an empty commit (live-found: recover rationale answer→finalize stalled).
        # This matches the NESTED arm above, which advances the cursor + dispatches only.
        lib.set_check_run(f"{PID}/{branch}/{gate}", sha, "completed", "success",
                          "Answered", f"Answered by @{actor}.")
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} gate {gate} answered -> {nxt_sub}")
        # Interactive gate: close the question issue now that it's fully answered.
        if issue_no:
            lib.close_issue(issue_no, f"Answered by @{actor} — resuming mental-model recovery.")
        lib.post_pr_comment(fb, f"{gate} answered by @{actor}; continuing to {nxt_sub}.")
        # Path-only dispatch: the unified `continue` handler requires NODE_PATH.
        # nxt_path is the next sub-state's full tree path (e.g. recover.rationale.finalize).
        lib.dispatch_continue(PID, INSTANCE, path=".".join(nxt_path))
    else:
        cur["state"] = "done"
        lib.dump_yaml(cf, cur)
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} gate {gate} answered -> leg done")
        if issue_no:
            lib.close_issue(issue_no, f"Answered by @{actor} — resuming mental-model recovery.")
        lib.fire_join_dispatch(PID, INSTANCE)
    print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                      "reason": "answer: complete"}))


# Unbranched start/reset on a fan-out protocol routes to the planner BEFORE the
# single-agent agent-unit discovery (which has no kind:"agent" state to read and
# would error). The branched fan-out path (continue with BRANCH set) and the
# single-agent path both fall through this guard unchanged.
if COMMAND == "answer":
    do_answer()
    sys.exit(0)

if COMMAND == "override":
    do_override()
    sys.exit(0)

if COMMAND == "resolve-gate":
    do_resolve_gate()
    sys.exit(0)

if COMMAND in ("start", "reset"):
    # Unified entry for EVERY protocol shape (single-agent, single-phase fanout,
    # multi-phase). enter_root seeds the first top-level node via the recursive
    # sequencer, creates _instance.yaml, applies labels, CAS-pushes, and emits.
    enter_root(COMMAND, HEAD_SHA)
    sys.exit(0)

# A `continue` whose tree path resolves to a fanout node dispatches that fanout's
# children matrix (nested fanouts are entered as their own engine invocation). A
# continue MUST carry NODE_PATH — it is the sole coordinate of the unified engine.
if COMMAND == "continue" and NODE_PATH:
    _p = NODE_PATH.split(".")
    _kind = paths.node_kind(proto_data, _p)
    if _kind == "fanout":
        # The established seed(emit=False)→cas_push→emit ordering: enter_node seeds
        # the leg files + nested __join.yaml marker locally, cas_push publishes them
        # to origin so the matrix legs (which re-checkout state) find them, THEN emit.
        branches = enter_node(proto_data, _p, "continue", emit=False)
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter nested fanout {NODE_PATH} (continue)")
        if not branches:
            # Empty dynamic-fanout short-circuit: a 0-leg materialization would emit
            # run-fanout with no legs; every GHA zone gates on legs != '[]' and skips,
            # so the enclosing join never fires and the instance STALLS. Fire THIS
            # fanout's join now (a vacuous barrier → all_terminal True → advance to
            # its .next), and emit a noop instead of the empty run-fanout. Path-keyed
            # only for a NESTED fanout; the top fanout stays path-less.
            lib.fire_join_dispatch(PID, INSTANCE, NODE_PATH if len(_p) > 1 else "")
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"empty-fanout:{NODE_PATH}"}))
            sys.exit(0)
        print(json.dumps(_fanout_action(proto_data, _p, branches)))
        sys.exit(0)
    if _kind == "agent":
        # A `continue` onto an AGENT node. Two shapes:
        #  - FRESH entry: a sub-pipeline sub-state (e.g. `report` after a nested join
        #    bubbled the cursor forward) — enter_node seeds it; cas_push so the
        #    dispatched agent finds it. iteration:1, feedback:"".
        #  - ITERATE re-dispatch: advance already advanced the SAME agent phase
        #    (iteration N + feedback history) and pushed it; enter_node PRESERVES it,
        #    so there is nothing new to push (an empty cas_push fails loudly). Carry
        #    the preserved iteration + last-failure feedback into the run-agent action.
        node = paths.node_at_path(proto_data, _p)
        seq = enter_node(proto_data, _p, "continue", emit=False)
        if seq.get("seeded", True):
            lib.cas_push(DIR, f"{PID}/{INSTANCE}: continue agent {NODE_PATH}")
        act = {"action": "run-agent",
               "iteration": seq.get("iteration", 1), "feedback": seq.get("feedback", ""),
               "reason": f"continue:{NODE_PATH}", "path": NODE_PATH,
               "workflow": node.get("workflow")}
        # Declared inputs come from the node AT THIS PATH — node_at_path is each-aware,
        # so it finds a DYNAMIC fanout's `each.states` sub-pipeline agent's inputs (e.g.
        # OCR main-review's `from: plan`). lib.state_inputs only scans top-level states +
        # a STATIC fanout's branches[], so it silently returns [] for a dynamic each →
        # the agent would receive empty inputs and not know which item it owns.
        declared = node.get("inputs", [])
        if declared:
            # Path-aware: resolve each `from` OUTERMOST-search relative to this
            # node's tree path, so a nested agent's inputs reach an earlier
            # nested-fanout leg's evidence (e.g. report ← analyze.sec/perf).
            act["inputs"] = lib.resolve_inputs(
                proto_data, DIR, PID, INSTANCE,
                consuming_branch=(_p[-2] if len(_p) >= 2 else None),
                consuming_phase=None, inputs=declared, consuming_path=_p)
        # A DYNAMIC-fanout leg's per-leg item is threaded by the expand matrix SEED
        # (stage_item + project_matrix_item at fan-out time), NOT by a declared `from:`.
        # The declared block above therefore misses it, so an ITERATE re-dispatch of a
        # leg whose sub-state declares no inputs (e.g. code-review's `triage`) would go
        # out with empty aw_context.inputs and the agent could not tell which item it
        # owns — it noops, the leg's checks fail, and the iterate loop can never converge.
        # Re-attach the staged item exactly as the fan-out surfaced it, reading the
        # durable <as>.item.json stage_item wrote on the state branch (idempotent: a
        # STATIC fanout has no expand → no `as` → skip; a plain agent has no enclosing
        # fanout → skip; the file is absent for anything that was never seeded).
        fpath = paths.enclosing_fanout_path(proto_data, _p)
        if fpath:
            exp = (paths.node_at_path(proto_data, fpath) or {}).get("expand") or {}
            as_ = exp.get("as")
            if as_:
                leg_path = _p[:len(fpath) + 1]          # fanout_path + this leg's id
                item_file = lib.output_artifact_path(
                    DIR, PID, INSTANCE,
                    path=lib.state_path(proto_data, leg_path), kind=f"{as_}.item")
                if os.path.isfile(item_file):
                    act.setdefault("inputs", []).append({"as": as_, "path": item_file})
        print(json.dumps(act))
        sys.exit(0)
    if _kind == "gate":
        # A `continue` onto a GATE sub-state: enter_node's gate arm opens the gate
        # (seeds the gate file + check-run + status comment); cas_push publishes.
        enter_node(proto_data, _p, "continue", emit=False)
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: continue gate {NODE_PATH} open")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"gate-open:{NODE_PATH}"}))
        sys.exit(0)
    if _kind == "merge":
        # A `continue` onto a MERGE state. Two shapes:
        #  - NESTED merge (a per-file `reduce`, path length > 1): LEG-TERMINAL.
        #  - TOP merge (path length 1, dispatched by the top join): finalize instance.
        node = paths.node_at_path(proto_data, _p)
        res = lib.run_merge_hook(DIR, PID, INSTANCE, PROTO, node, consuming_path=_p)
        if len(_p) > 1:
            # NESTED merge (a per-file `reduce`): LEG-TERMINAL, mirroring
            # advance.complete_sequence. (1) persist the merge result as THIS leg's
            # output evidence so the enclosing fanout's from_fanout can collect the
            # survivors; (2) mark the file-leg SEQUENCE CURSOR done; (3) fire the
            # enclosing fanout's join exactly as join.py does (path-less for a
            # top-level enclosing fanout, path-keyed if nested).
            leg_path = _p[:-1]                       # the file-leg sub-pipeline cursor
            ev = lib.output_artifact_path(DIR, PID, INSTANCE, path=lib.state_path(proto_data, _p))
            os.makedirs(os.path.dirname(ev), exist_ok=True)
            with open(ev, "w") as f:
                json.dump(res, f)
            cursor_sf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto_data, leg_path))
            cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
            cur["state"] = "done"
            lib.dump_yaml(cursor_sf, cur)
            lib.cas_push(DIR, f"{INSTANCE}: nested merge {'.'.join(_p)} → leg done")
            efp = paths.enclosing_fanout_path(proto_data, _p)
            fields = {"protocol": PID, "instance": INSTANCE}
            if efp and len(efp) > 1:
                fields["path"] = ".".join(efp)
            lib._gh_dispatch("protocol-join", fields)
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"nested-merge-done:{'.'.join(_p)}"}))
            sys.exit(0)
        # TOP merge — Option-B gate: honor on_blocked / .next before finalizing.
        pr = lib.pr_from_instance(INSTANCE)
        inf = lib.instance_file(DIR, PID, INSTANCE)
        inst = lib.load_yaml(inf) if os.path.isfile(inf) else {}
        # A crashed/unresolved/bad-JSON hook returns a NEUTRAL res with no `blocked`
        # key. On a gate that opted into halting, a non-genuine verdict must FAIL
        # CLOSED (halt), never fall through to .next — otherwise a security-critical
        # gate (on_blocked:halt + next:post-fix) fails OPEN on a crash. A GENUINE
        # `failure` conclusion (blocked not set) must halt too: a reducer that
        # reports failure without flipping `blocked` would otherwise fall through.
        genuine = res.get("conclusion") in ("success", "failure")
        if node.get("on_blocked") == "halt" and (
                res.get("blocked") or res.get("conclusion") == "failure" or not genuine):
            # A required gate returned blocked:true (or a non-genuine/crashed verdict)
            # → HALT before .next. Record the `halted` marker do_override reads
            # (advance.py:728 shape), fail the check-run, label 'blocked', and stop.
            # `/override` clears it and continues to next_sibling(honesty-gate) == post-fix.
            inst["halted"] = {"phase": _p[-1], "reason": "blocked", "sha": HEAD_SHA}
            lib.dump_yaml(inf, inst)
            gsum = res.get("summary", "") or "a required gate did not pass"
            lib.set_check_run(PID, HEAD_SHA, "completed", "failure", "Gate blocked", gsum)
            lib.post_pr_comment(pr, f"⛔ **{_p[-1]}** gate blocked: {gsum}. "
                                    f"A write-access user can comment `/override <reason>` to proceed.")
            lib.upsert_status_comment(inf, pr, lib.render_instance_status_body(DIR, PID, INSTANCE, PROTO))
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "blocked")
            lib.cas_push(DIR, f"{INSTANCE}: merge {_p[-1]} blocked → pipeline halted")
            print(json.dumps({"action": "halt", "iteration": 0, "feedback": "",
                              "reason": f"merge-blocked:{_p[-1]}"}))
            sys.exit(0)
        if node.get("next"):
            # Not blocked and the gate names a successor → CONTINUE past the merge
            # instead of finalizing (multi-phase after a top merge, e.g. post-fix).
            nxt = node["next"]
            # Persist the merge rollup as THIS gate phase's output evidence so a
            # downstream `{from:'<this-gate>'}` agent input resolves (e.g. mrp's
            # {from:'honesty-gate', as:'honesty'} → aggregate-honesty's
            # {conclusion,summary,blocked,rollup}). Mirrors the nested-merge arm above;
            # the path is state_path(proto, _p) — byte-identical to what resolve_inputs
            # computes for a top-level `from` — and is written BEFORE cas_push so it
            # lands on the state branch with the rest of this transition.
            ev = lib.output_artifact_path(DIR, PID, INSTANCE, path=lib.state_path(proto_data, _p))
            os.makedirs(os.path.dirname(ev), exist_ok=True)
            with open(ev, "w") as f:
                json.dump(res, f)
            concl, summary, _label = lib.finalize_merge_result(res)
            inst["phase"] = nxt
            lib.dump_yaml(inf, inst)
            lib.set_check_run(PID, HEAD_SHA, "completed", concl, "Combined", summary)
            lib.post_pr_comment(pr, f"🧬 **{_p[-1]}**: {summary}")
            lib.upsert_status_comment(inf, pr, lib.render_instance_status_body(DIR, PID, INSTANCE, PROTO))
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, nxt)
            lib.cas_push(DIR, f"{INSTANCE}: merge {_p[-1]} clear → continue {nxt}")
            lib.dispatch_continue(PID, INSTANCE, path=nxt)
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"merge-continue:{nxt}"}))
            sys.exit(0)
        # TOP merge (no .next): finalize the instance (existing behavior).
        inst["phase"] = _p[-1]
        inst["joined"] = True
        lib.dump_yaml(inf, inst)
        concl, summary, label = lib.finalize_merge_result(res)
        lib.set_check_run(PID, HEAD_SHA, "completed", concl, "Combined", summary)
        lib.post_pr_comment(pr, f"🧬 **{_p[-1]}**: {summary}")
        lib.upsert_status_comment(inf, pr, lib.render_instance_status_body(DIR, PID, INSTANCE, PROTO))
        lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, label)
        lib.cas_push(DIR, f"{INSTANCE}: merge {_p[-1]} → done")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"merge:{_p[-1]}"}))
        sys.exit(0)

# A `continue` reaching here carried no resolvable NODE_PATH coordinate. The
# unified engine has a single coordinate (NODE_PATH); start/reset routed to
# enter_root above and a continue must name the node it resumes.
if COMMAND == "continue":
    sys.stderr.write("[next] 'continue' requires a NODE_PATH\n")
    sys.exit(2)

sys.stderr.write(f"[next] unknown command: {COMMAND}\n")
sys.exit(2)

#!/usr/bin/env python3
# next.py <state_workdir> <instance-key> <protocol.json> <command> [head_sha]
# Pure planner: reads (state, protocol, command), emits an action JSON on stdout.
# The WORKFLOW decides what an event means and passes a command; the planner never
# sniffs events. Commands:
#   start / reset   enter the protocol from its first top-level node via enter_root
#                   (start/reset both seed a fresh run; reset is invoked when a new
#                   head commit invalidates the old run).
#   continue        resume the leg named by NODE_PATH (the SOLE coordinate of the
#                   unified engine) — seed/dispatch the fork/agent/human-task/code node it
#                   resolves to. A continue WITHOUT a resolvable NODE_PATH errors.
#   answer / override / resolve-human-task   human-task commands (path-aware).
# head_sha (optional) is recorded as instance metadata (the check-run target); it is
# NEVER compared to decide policy — that decision lives in the workflow.
import json
import os
import re
import sys

# The script's directory is sys.path[0], so `import lib` finds lib.py alongside.
import lib
import paths


def _code_step_outcome(res):
    """A `code` hook's result -> the (exit, summary) recorded as its step outcome.

    NOT `res["exit"]`. `lib.finalize_code_result` is the engine's ONE rule for
    reading a code hook's verdict, and it is deliberately not the raw exit code:
    a deliberate `conclusion` wins over the exit code, while an ABSENT conclusion
    is success only when the hook exited clean. A hook that exits 0 while
    reporting `conclusion: "failure"` -- code-review-ocr's post-review on
    REQUEST_CHANGES, the topmerge fixture's `fail` mode -- is an OBJECTION, and
    reading the raw exit silently greened it.

    One definition, used by every `code` arm (leg-terminal, group-terminal,
    mid-pipeline, finalize), because three of them disagreed with the fourth and
    only the fourth had a test.
    """
    concl, summary, _label = lib.finalize_code_result(res)
    return (0 if concl == "success" else 1), summary

DIR = sys.argv[1]
INSTANCE = sys.argv[2]
PROTO = sys.argv[3]
COMMAND = sys.argv[4]
HEAD_SHA = sys.argv[5] if len(sys.argv) > 5 else ""
# NODE_PATH (NOT PATH — that is the OS executable search path) is the dot-joined
# tree-navigation path of a `continue` dispatch. It is the SOLE coordinate of the
# unified engine: when it resolves to a fork node the planner emits that fork's
# children matrix (a nested fork is dispatched as its own engine invocation), to
# an agent it seeds + emits run-agent, to a human task it opens the human task, to a merge it
# runs the reduce hook. start/reset ignore it (they route to enter_root).
NODE_PATH = os.environ.get("NODE_PATH", "")

# validate=True validates the AUTHOR's tree as well as the normalized one. The
# author's tree is the only one carrying bare `agent`/`code` legs, so it is the
# only one on which `lib._validate_leg`'s rules can fire — see load_protocol.
# The depth guard runs on the returned (NORMALIZED) tree, since the wrap adds a
# level. It runs after validation now that both share one load; tests/fixtures/
# too-deep is otherwise valid, so its max_depth message is unchanged.
try:
    proto_data = lib.load_protocol(PROTO, validate=True)
    lib.check_depth(proto_data)
except ValueError as _e:
    sys.stderr.write(f"[next] {_e}\n")
    sys.exit(2)

PID = proto_data["name"]  # equivalent to lib.protocol_id(PROTO); proto_data already loaded

# Check out the state branch first: both the fan-out planner (below) and the
# single-agent path write into DIR, and state_checkout only depends on DIR,
# so doing it here is behaviour-preserving for the single-agent path.
lib.state_checkout(DIR)


def _fork_action(proto, path, branches):
    """Build the run-fork action dict for the fork at `path`. Single-phase
    keeps reason='fork' with NO phase key; multi-phase uses reason='phase:<id>'
    and adds the phase key.

    `branches` is emitted UNCHANGED — every seeded leg, agent-first or
    code-first alike — but NOTHING READS IT: it is a historical/diagnostic key,
    not a contract (`git grep branches .github/workflows/agentic-{engine,
    orchestrator}.yml` is empty, and tests/engine/test_fork_entry_code_leg.py
    pins that). `legs` is the key the GHA agent matrix actually dispatches
    from, and it is NOT a 1:1 mirror of `branches`.

    C1: a leg whose first child is an INLINE `code` node (script:, no
    `workflow`) has nothing for the GHA matrix (agentic-engine.yml) to
    dispatch — it hard-exits on a leg entry carrying `workflow: null`. Such a
    leg's step already executes correctly once REACHED via a `continue` hop
    (test_mid_leg_code.py's mid-leg `code` arm is the same mechanism) — the
    only defect was fork ENTRY routing it into the matrix instead. So an
    INLINE code-first leg is partitioned OUT of `legs[]` and dispatched here
    via `lib.dispatch_continue`, exactly the call the mid-leg hop already
    makes. An agent-first leg OR a DISPATCHED-code-first leg (lib.is_dispatched_code
    — it DOES carry `workflow`, unlike the inline case) is unaffected and still
    lands in `legs[]` for the matrix: both run in the same agent lane."""
    multi = lib.is_multiphase(proto)
    act = {"action": "run-fork", "iteration": 1, "feedback": "",
           "reason": (f"phase:{path[-1]}" if multi else "fork")}
    if multi:
        act["phase"] = path[-1]
    act["branches"] = branches
    # `legs` is the path-aware companion to `branches` (Stage 3/4b): one entry per
    # AGENT-FIRST child carrying its full LEAF tree path + agent workflow.
    # Leaf path = fork_path + leg_id + first_substate. Every leg is a sequence
    # (normalized at load), so the substate is always present — there is no
    # flat-leg case where the leg id is itself the leaf.
    legs = []
    for b in branches:
        leaf = path + [b["id"], b["substate"]]
        leaf_path = ".".join(leaf)
        leaf_node = paths.node_at_path(proto, leaf)
        leaf_kind = leaf_node.get("kind")
        in_agent_lane = leaf_kind == "agent" or lib.is_dispatched_code(leaf_node)
        if not in_agent_lane:
            # The leg's first substate is NOT in the agent lane: an INLINE code
            # node (script:, no workflow), a nested fork, a human task, a choice —
            # nothing for the agent matrix to dispatch (agentic-engine.yml
            # hard-exits on a leg entry carrying `workflow: null`). Route it
            # through the SAME continue-dispatch a mid-leg/leg-terminal node
            # already uses. An agent-first leg OR a DISPATCHED-code-first leg
            # (lib.is_dispatched_code — it DOES carry `workflow`, unlike the
            # inline case) falls through to the matrix branch below instead.
            lib.dispatch_continue(PID, INSTANCE, path=leaf_path)
            continue
        leg = {"path": leaf_path, "workflow": b.get("workflow"),
               "lane": "code" if lib.is_dispatched_code(leaf_node) else "agent"}
        if b.get("inputs"):            # dynamic legs only; static branches never carry this
            leg["inputs"] = b["inputs"]
        legs.append(leg)
    act["legs"] = legs
    lib.check_matrix_size(legs)
    return act


def _seed_or_preserve_leaf(proto, path, node, command):
    """Seed (or preserve, on an in-flight iterate) the state file for a leaf
    node running in the AGENT LANE: an `agent` node, or a `code` node
    DISPATCHED as a workflow (lib.is_dispatched_code) — both are dispatched
    the same way (agentic-engine.yml's matrix runs them, checks verify their
    evidence, advance.py records their verdict), so they share ONE seeding
    contract. Extracted from enter_node's former `kind=="agent"`-only arm so
    the continue dispatcher's dispatched-code arm reuses it instead of a
    second copy — a second copy is exactly how this file's bugs got in.

    An iterate re-dispatch is a `continue` onto the SAME node that advance
    already advanced (iteration N + a history entry carrying the failed
    round's feedback). Re-seeding it to iteration:1/history:[] would reset
    the bounded iterate loop into an INFINITE one (the counter never reaches
    max_iterations) and drop the feedback the agent/hook needs to converge.
    So only a FRESH entry (no state file, or a prior terminal, or a
    non-continue command) seeds; an in-flight continue preserves.

    Returns {"id","workflow","iteration","feedback","seeded"}: `seeded` tells
    a `continue` caller whether new state was written (so it must cas_push
    it) or an in-flight iterate was preserved (nothing new → an empty
    cas_push would fail loudly)."""
    fpath = lib.state_path(proto, path)
    sf = lib.state_file(DIR, PID, INSTANCE, path=fpath)
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    life = paths.enclosing_fork_id(proto, path)
    node_state = life or path[-1]
    existing = lib.load_yaml(sf) if os.path.exists(sf) else None
    preserve = bool(command == "continue" and existing
                    and existing.get("state") == node_state)
    if preserve:
        st = existing
    else:
        st = {"protocol": PID, "instance": INSTANCE, "state": node_state,
              "iteration": 1, "human_task": {}, "head_sha": HEAD_SHA, "history": []}
        lib.dump_yaml(sf, st)
    it = int(st.get("iteration", 1))
    _hist = st.get("history") or []
    fb = _hist[-1].get("feedback", "") if _hist else ""
    return {"id": path[-1], "workflow": node.get("workflow"),
            "iteration": it, "feedback": fb, "seeded": not preserve}


def _resolve_from_fork_input(proto, path, inp):
    """A dispatched `code` node's `inputs[].from_fork` entry -> {as, path} whose
    file holds lib.collect_fork_evidence's reduced rows for that fork, so a
    dispatched fork-reducer has the same expressive power as an inline one
    (lib.run_code_hook). REUSES collect_fork_evidence — the SAME reducer the
    inline lane calls — rather than a second fork-collection path; that
    duplication is exactly how the from_fork/resolve_inputs KeyError crash (a
    dispatched code node routed through the AGENT lane's resolve_inputs, which
    only understands `from`) got in.

    Fork tree-path resolution mirrors run_code_hook byte-for-byte: NESTED (this
    node's own path has more than one element — it lives inside a sequence/leg)
    resolves the fork as a SIBLING in the same enclosing scope
    (`path[:-1] + [fork_id]`); TOP-level resolves it at the root (`[fork_id]`).
    """
    fo_id = inp["from_fork"]
    nested = bool(path) and len(path) > 1
    fo_tree_path = list(path[:-1]) + [fo_id] if nested else [fo_id]
    fo_node = paths.node_at_path(proto, fo_tree_path)
    if fo_node is None or not lib.fork_is_materialized(DIR, PID, INSTANCE, fo_tree_path, fo_node):
        raise ValueError(
            f"dispatched code '{'.'.join(path)}' input from_fork='{fo_id}': "
            f"no manifest at {'.'.join(fo_tree_path)} (fork not materialized "
            f"or misnamed)"
        )
    rows = lib.collect_fork_evidence(DIR, PID, INSTANCE, fo_tree_path, fo_node, proto=proto)
    out_fp = lib.output_artifact_path(
        DIR, PID, INSTANCE, path=lib.state_path(proto, path), kind=f"{inp['as']}.fromfork")
    os.makedirs(os.path.dirname(out_fp), exist_ok=True)
    with open(out_fp, "w") as f:
        json.dump(rows, f)
    return {"as": inp["as"], "path": out_fp}


def _run_agent_action(proto, path, node, seq):
    """Build the run-agent action dict for a leaf already seeded/preserved by
    _seed_or_preserve_leaf: declared `inputs` resolution (path-aware, via
    lib.resolve_inputs) + a dynamic-fork leg's staged-item re-attachment.
    Shared by the continue dispatcher's `agent` arm and its DISPATCHED `code`
    arm (lib.is_dispatched_code) — both run in the same agent lane, and a
    second copy of this plumbing is exactly how this file's bugs got in."""
    act = {"action": "run-agent",
           "iteration": seq.get("iteration", 1), "feedback": seq.get("feedback", ""),
           "reason": f"continue:{'.'.join(path)}", "path": ".".join(path),
           "workflow": node.get("workflow"),
           "lane": "code" if lib.is_dispatched_code(node) else "agent"}
    # Declared inputs come from the node AT THIS PATH — node_at_path is each-aware,
    # so it finds a DYNAMIC fork's `each.states` sub-pipeline agent's inputs (e.g.
    # OCR main-review's `from: plan`). lib.state_inputs only scans top-level states +
    # a STATIC fork's branches[], so it silently returns [] for a dynamic each →
    # the agent would receive empty inputs and not know which item it owns.
    declared = node.get("inputs", [])
    if declared:
        # lib.resolve_inputs only understands `from` — a `from_fork` entry has
        # no `from` key and would raise KeyError. `from_fork` is legal ONLY on
        # a dispatched code node (validate_protocol's kind=="code" rule 6); an
        # agent node's `inputs` therefore keeps calling resolve_inputs on the
        # UNFILTERED list, unchanged from before — this split only engages for
        # `lib.is_dispatched_code(node)`.
        if lib.is_dispatched_code(node):
            plain = [inp for inp in declared if "from" in inp]
            resolved = list(lib.resolve_inputs(
                proto, DIR, PID, INSTANCE,
                consuming_branch=(path[-2] if len(path) >= 2 else None),
                consuming_phase=None, inputs=plain, consuming_path=path)) if plain else []
            for inp in declared:
                if inp.get("from_fork"):
                    resolved.append(_resolve_from_fork_input(proto, path, inp))
            if resolved:
                act["inputs"] = resolved
        else:
            # Path-aware: resolve each `from` OUTERMOST-search relative to this
            # node's tree path, so a nested agent's inputs reach an earlier
            # nested-fork leg's evidence (e.g. report ← analyze.sec/perf).
            act["inputs"] = lib.resolve_inputs(
                proto, DIR, PID, INSTANCE,
                consuming_branch=(path[-2] if len(path) >= 2 else None),
                consuming_phase=None, inputs=declared, consuming_path=path)
    # A DYNAMIC-fork leg's per-leg item is threaded by the expand matrix SEED
    # (stage_item + project_matrix_item at fan-out time), NOT by a declared `from:`.
    # The declared block above therefore misses it, so an ITERATE re-dispatch of a
    # leg whose sub-state declares no inputs (e.g. code-review's `triage`) would go
    # out with empty aw_context.inputs and the agent could not tell which item it
    # owns — it noops, the leg's checks fail, and the iterate loop can never converge.
    # Re-attach the staged item exactly as the fan-out surfaced it, reading the
    # durable <as>.item.json stage_item wrote on the state branch (idempotent: a
    # STATIC fork has no expand → no `as` → skip; a plain agent has no enclosing
    # fork → skip; the file is absent for anything that was never seeded).
    fpath = paths.enclosing_fork_path(proto, path)
    if fpath:
        exp = (paths.node_at_path(proto, fpath) or {}).get("expand") or {}
        as_ = exp.get("as")
        if as_:
            leg_path = path[:len(fpath) + 1]          # fork_path + this leg's id
            item_file = lib.output_artifact_path(
                DIR, PID, INSTANCE,
                path=lib.state_path(proto, leg_path), kind=f"{as_}.item")
            if os.path.isfile(item_file):
                act.setdefault("inputs", []).append({"as": as_, "path": item_file})
    return act


def enter_node(proto, path, command):
    """Recursive sequencer: SEED the node at the tree-navigation `path` and
    return what its caller needs to emit. It does not print.

    The recursive sequencer for the unified engine: enter_root and the NODE_PATH
    `continue` arms call it. INSTANCE-file / phase-label / cas_push side-effects
    stay with those callers — this function only seeds the node's own state
    file(s). Every file call routes the tree path through lib.state_path
    (single-phase drops the leading top fork id), so depth-<=3 files keep their
    historical layout.

    Seeding and emitting are split because EVERY caller has to cas_push between
    them: the state must be on the branch before anything is dispatched against
    it. An `emit` flag used to make this optional, but every call site passed
    emit=False, so the emitting half was dead code that still had to be kept in
    sync with `_emit_for_node` by hand — the second partial switch the BPMN plan
    set out to remove. `_emit_for_node` is now the one emitter.

    `path` is rooted at the top phase/fork id; e.g. the top fork enters as
    [fork_id]. `command` is carried for parity with the recursive callers."""
    kind = paths.node_kind(proto, path)
    node = paths.node_at_path(proto, path)
    life = paths.enclosing_fork_id(proto, path)
    fpath = lib.state_path(proto, path)
    if kind == "sequence":
        first = paths.first_child_id(node)
        cf = lib.state_file(DIR, PID, INSTANCE, path=fpath)
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        lib.dump_yaml(cf, {"protocol": PID, "instance": INSTANCE, "state": life,
                           "sub_state": first, "iteration": 1, "human_task": {}, "history": []})
        return enter_node(proto, path + [first], command)
    if kind == "fork":
        # Reset the join barrier on ENTERING this fork so its own join can fire.
        # NESTED forks (len > 1) use a path-keyed __join.yaml marker. A TOP-level
        # fork (len 1) uses the instance-wide _instance.yaml `joined` flag — which a
        # PRIOR top-level fork (e.g. `review` before `post-fix`) leaves latched True;
        # without this reset join.py would no-op the second fork's barrier and the
        # pipeline would stall (the next phase, e.g. mrp, never dispatched). Idempotent
        # for the first fork (joined already absent/False). The change is staged with
        # the seeded legs and CAS-pushed by the fork-entry caller.
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
            # --- DYNAMIC fork: materialize legs from the expander manifest. ---
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
            # zero legs → branches == [] falls through the shared tail unchanged (vacuous fork)
        else:
            branches = [_seed_child(proto, path + [b["id"]], b) for b in node.get("branches", [])]
        # Return the branch emit-dicts so the caller prints the run-fork AFTER
        # its own instance-file / label / cas_push side-effects (preserving the
        # legacy seed→side-effects→cas_push→emit ordering).
        return branches
    if kind == "agent":
        return _seed_or_preserve_leaf(proto, path, node, command)
    if paths.is_human_task(kind):
        pr = lib.pr_from_instance(INSTANCE)
        lib.open_human_task(DIR, PID, INSTANCE, PROTO, path[-1], HEAD_SHA, pr,
                      phase=(path[-1] if lib.is_multiphase(proto) else None),
                      tree_path=list(path))
        return None
    if kind in ("code", "choice"):
        # A DETERMINISTIC node in ENTRY position — a protocol may begin with one
        # ("a protocol whose only step is a code hook runs to done with no
        # agent"), and a group's first child may be one. This applies to BOTH
        # `code` modes alike (inline `script:` and DISPATCHED `workflow:`,
        # lib.is_dispatched_code) — which lane a `code` node runs in is the
        # `continue` dispatcher's call, not entry's. Entering it must not
        # re-implement what the `continue` dispatcher already does: running a
        # code hook (and its blocked/next/group-done handling), seeding a
        # dispatched code node into the agent lane, and resolving a choice are
        # its job, and a second copy is exactly how this branch's bugs got in.
        # So seed the cursor and hand off, mirroring advance.py's kind-agnostic
        # arm — the follow-on continue executes the node.
        #
        # Nothing to write here: a code/choice node owns no pre-run state (the
        # cursor that names it was written by whoever routed here — the instance
        # file for a root node, the group's cursor for a grouped one). The
        # hand-off dispatch is the CALLER's, so it lands after the caller's
        # cas_push — the seeded state must be on the branch before anything is
        # dispatched against it (see _emit_for_node's matching arm).
        return {"dispatch_continue": ".".join(path)}
    # No arm matched. Every kind must be handled here; a silent `return None`
    # would seed nothing and stall the instance with no error anywhere. Loud, so
    # adding a kind without wiring this site is a test failure, not a mystery.
    raise ValueError(
        f"unhandled node kind {kind!r} at path {'.'.join(path)!r} — "
        f"enter_node has no arm for it")


def _seed_child(proto, path, cfg):
    """Seed one fan-out child and return its run-fork branch dict.

    Every leg is a `sequence` (lib.normalize_protocol wraps a bare agent/code
    leg at load), so there is exactly one shape here: a cursor file naming the
    first child, plus that child's own state file. The flat arm this function
    used to carry — one file in which a leg's cursor, history and output all
    coincided — is gone with the shape that needed it.
    """
    life = paths.enclosing_fork_id(proto, path)
    first = paths.first_child_id(cfg)
    cf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, path))
    os.makedirs(os.path.dirname(cf), exist_ok=True)
    lib.dump_yaml(cf, {"protocol": PID, "instance": INSTANCE, "state": life,
                       "sub_state": first, "iteration": 1, "human_task": {}, "history": []})
    sf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, path + [first]))
    lib.dump_yaml(sf, {"protocol": PID, "instance": INSTANCE, "state": life,
                       "iteration": 1, "human_task": {}, "head_sha": HEAD_SHA, "history": []})
    fc = paths.node_at_path(proto, path + [first])
    return {"id": path[-1], "workflow": fc.get("workflow"),
            "substate": first, "iteration": 1, "feedback": "",
            "lane": "code" if lib.is_dispatched_code(fc) else "agent"}


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
    # human task" does not orphan it (the wipe below drops our tracking of it).
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
    value from enter_node — the branch emit-dicts for a fork, the seeded-state
    dict for an agent, None for a human task.

    THE emitter. enter_node used to carry a parallel copy behind an `emit` flag
    that every caller passed False; the two were kept in sync by hand, which is
    exactly the hazard the BPMN plan called out. The dead half is gone."""
    kind = paths.node_kind(proto_data, path)
    if kind == "fork":
        print(json.dumps(_fork_action(proto_data, path, branches)))
        return
    if paths.is_human_task(kind):
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"human-task-open:{path[-1]}"}))
        return
    if kind == "sequence":
        # enter_node descended into the first child; emit for THAT leaf, not for
        # the container (which has no workflow of its own).
        first = paths.first_child_id(paths.node_at_path(proto_data, path))
        if first:
            _emit_for_node(path + [first], branches)
            return
        raise ValueError(f"sequence {'.'.join(path)!r} has no children to enter")
    if kind in ("code", "choice"):
        # A deterministic node in ENTRY position. enter_node deliberately does
        # not execute it (the `continue` dispatcher owns that, and a second copy
        # is how this branch's bugs got in) — it hands off. The dispatch happens
        # HERE rather than in enter_node because enter_root cas_pushes between
        # the two: the seeded state must be on the branch before anything is
        # dispatched against it.
        lib.dispatch_continue(PID, INSTANCE, path=".".join(path))
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"enter-{kind}:{'.'.join(path)}"}))
        return
    if kind != "agent":
        # Previously this fell through to the agent arm and emitted
        # {"action": "run-agent", "workflow": null}, which agentic-engine.yml
        # hard-fails on later, in a different job, as an opaque gh-aw error.
        raise ValueError(
            f"unhandled node kind {kind!r} at path {'.'.join(path)!r} — "
            f"_emit_for_node has no arm for it")
    node = paths.node_at_path(proto_data, path)
    act = {"action": "run-agent", "iteration": 1, "feedback": "",
           "reason": f"phase:{path[-1]}",
           "path": ".".join(path),
           "workflow": node.get("workflow"),
           "lane": "agent"}
    if lib.is_multiphase(proto_data):
        act["phase"] = path[-1]
    print(json.dumps(act))


def enter_root(command, head_sha):
    """Unified entry for start/reset: seed the FIRST top-level node via the
    recursive sequencer, create _instance.yaml, apply labels, CAS-push, and emit
    the node's action. The single entry point for EVERY protocol shape
    (single-agent, single-phase fork, multi-phase)."""
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
    branches = enter_node(proto_data, [first], command)
    lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter root phase {first} ({command})")
    _emit_for_node([first], branches)


def do_override():
    """HITL escape-hatch: a write-access human forces a *blocked* node to advance
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
        # Resolve from the recorded PATH; fall back to the bare id for a marker
        # written before `path` existed (an in-flight instance mid-upgrade).
        blocked_path = (halted.get("path") or "").split(".") if halted.get("path") \
            else [blocked_phase]
        # The successor lives in the blocked node's OWN scope — a halt inside a
        # group resumes inside that group, not at the root. If the blocked node
        # was LAST in its scope, the scope itself is what continues, so walk
        # outward until something follows (a group last in a group, and so on).
        _scan = blocked_path
        nxt = None
        while _scan:
            nxt = paths.next_sibling(proto_data, _scan)
            if nxt:
                break
            _scan = paths.parent_path(_scan)
        if not nxt:
            refuse("The blocked node is the final phase; there is nothing to advance to.",
                   "override: no next phase")
            return
        nxt_path = paths.parent_path(_scan) + [nxt]
        actor = os.environ.get("OVERRIDE_ACTOR", "")
        reason = os.environ.get("OVERRIDE_REASON", "")
        inst.setdefault("overrides", []).append(
            {"phase": blocked_phase, "actor": actor, "reason": reason})
        inst.pop("halted", None)
        # Advance the root cursor to `nxt` and dispatch a path-continue; the
        # continue dispatch will seed+enter the next phase via the NODE_PATH guard.
        # Note: _instance.yaml's head_sha stays the instance-seed head (as before —
        # the authoritative head is recorded per-phase in each phase's own state file).
        # Only a ROOT-level resume moves the root cursor; a resume inside a group
        # moves that group's cursor, which the continue's own entry writes.
        if len(nxt_path) == 1:
            inst["phase"] = nxt
        lib.dump_yaml(inf, inst)
        note = f"⚠️ {blocked_phase} was blocked — overridden by @{actor}; proceeding to {nxt}."
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr,
                               nxt if len(nxt_path) == 1 else nxt_path[0])
        lib.cas_push(DIR, f"{INSTANCE}: {blocked_phase} overridden by {actor} → continue {nxt}")
        lib.dispatch_continue(PID, INSTANCE, path=".".join(nxt_path))
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"override:continue:{nxt}"}))
        return

    # Not a blocked halt → give a precise message: exhausted vs simply not-halted.
    cursor = inst.get("phase") or ""
    cursor_sf = lib.state_file(DIR, PID, INSTANCE, phase=cursor) if cursor else ""
    cursor_state = (lib.load_yaml(cursor_sf).get("state")
                    if cursor_sf and os.path.isfile(cursor_sf) else "")
    if cursor_state == "failed":
        refuse(f"The {cursor} node is exhausted (it could not produce a valid result), "
               f"not blocked. Override only applies to a human task that ran and returned a "
               f"blocking verdict; re-run the pipeline instead.",
               "override: exhausted")
    else:
        refuse("Nothing to override — the pipeline is not currently halted at a "
               f"blocked node (current phase: {cursor}).",
               "override: not halted")


def do_resolve_human_task():
    """Human approval-task resolution. write/admin auth happened in the workflow;
    next.py sees only an authorized actor. Reads HUMAN_TASK_DECISION/ACTOR/REASON/PR_AUTHOR
    from env, mutates the cursor node's `human_task` record, and advances (approve) or
    halts (request-changes / reject). Guards refuse with one PR comment + a halt
    action — no state change. A human task is 'live' when human_task.state in {open,
    changes_requested}."""
    pr = lib.pr_from_instance(INSTANCE)
    inf = lib.instance_file(DIR, PID, INSTANCE)
    decision = os.environ.get("HUMAN_TASK_DECISION", "")
    actor = os.environ.get("HUMAN_TASK_ACTOR", "")
    reason = os.environ.get("HUMAN_TASK_REASON", "")
    pr_author = os.environ.get("HUMAN_TASK_PR_AUTHOR", "")

    def refuse(message, code):
        lib.post_pr_comment(pr, message)
        print(json.dumps({"action": "halt", "iteration": 0, "feedback": "", "reason": code}))

    if not os.path.isfile(inf):
        refuse(f"Nothing to resolve — no {PID} run exists for this PR.", "human-task: no instance")
        return
    inst = lib.load_yaml(inf)
    cursor = inst.get("phase") or ""
    cur_state = lib.state_by_id(proto_data, cursor)
    if not cursor or not cur_state or cur_state.get("kind") != "approval":
        refuse(f"Nothing to resolve — no approval task is currently open for this PR "
               f"(current phase: {cursor or 'none'}).", "human-task: none open")
        return

    sf = lib.state_file(DIR, PID, INSTANCE, phase=cursor)
    gdata = lib.load_yaml(sf) if os.path.isfile(sf) else {}
    g = gdata.get("human_task") or {}
    gstate = g.get("state", "")
    sha = gdata.get("head_sha", "") or HEAD_SHA
    # A root-level human task is a STEP of the root sequence, so it publishes no
    # check-run of its own: it records an outcome, and the ROOT publishes.
    task_path = lib.state_path(proto_data, [cursor])

    if gstate == "rejected":
        refuse("This approval was rejected; push a new commit or comment `/review` to "
               "restart the pipeline.", "human-task: rejected")
        return
    if gstate not in ("open", "changes_requested"):
        refuse(f"Nothing to resolve — the {cursor} node is not awaiting a decision "
               f"(state: {gstate or 'unknown'}).", "human-task: not live")
        return
    if (decision == "approve" and cur_state.get("approve_excludes_author")
            and actor and actor == pr_author):
        refuse(f"@{actor} the PR author cannot approve their own request; another "
               f"write-access reviewer must `/approve`.", "human-task: self-approve")
        return

    g.setdefault("history", []).append({"decision": decision, "actor": actor, "reason": reason})

    if decision == "approve":
        g["state"] = "approved"
        gdata["human_task"] = g
        lib.dump_yaml(sf, gdata)
        lib.node_outcome(DIR, PID, INSTANCE, task_path, 0, f"approved by @{actor}")
        nxt = paths.next_sibling(proto_data, [cursor])
        if nxt:
            note = f"✅ {cursor} approved by @{actor}; proceeding to {nxt}."
            if reason:
                note += f"\n\n> {reason}"
            lib.post_pr_comment(pr, note)
            # Advance the root cursor to `nxt` and dispatch a path-continue; the
            # continue dispatch seeds+enters the next phase (fan-out, agent, or human task)
            # via the NODE_PATH guard in next.py — path-based like the rest of the
            # unified engine.
            inst = lib.load_yaml(inf)
            inst["phase"] = nxt
            lib.dump_yaml(inf, inst)
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, nxt)
            lib.cas_push(DIR, f"{INSTANCE}: {cursor} approved by {actor} → continue {nxt}")
            lib.dispatch_continue(PID, INSTANCE, path=nxt)
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"human-task:approved:{cursor}:continue:{nxt}"}))
            # Deliberately NO publish here: the root sequence has NOT finished —
            # `nxt` still has to run. Completing the aggregate now would let the
            # merge box go green on work that has not happened (HOW-IT-WORKS 5.1).
        else:
            # The human task was the LAST root step → the root sequence finished.
            lib.publish_check_run(DIR, PID, INSTANCE, proto_data, [], sha)
            note = f"✅ {cursor} approved by @{actor}; pipeline complete."
            if reason:
                note += f"\n\n> {reason}"
            lib.post_pr_comment(pr, note)
            body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
            lib.upsert_status_comment(inf, pr, body)
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "done")
            lib.cas_push(DIR, f"{INSTANCE}: {cursor} approved by {actor} → done")
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"human-task:approved:{cursor}"}))
        return

    if decision == "request-changes":
        g["state"] = "changes_requested"
        gdata["human_task"] = g
        lib.dump_yaml(sf, gdata)
        lib.node_outcome(DIR, PID, INSTANCE, task_path, 1,
                         f"changes requested by @{actor}")
        lib.publish_check_run(DIR, PID, INSTANCE, proto_data, [], sha)
        body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
        lib.upsert_status_comment(inf, pr, body)
        note = (f"🔁 {cursor} — changes requested by @{actor}. Push a new commit to "
                f"re-run the pipeline, or a reviewer can `/approve`.")
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        lib.cas_push(DIR, f"{INSTANCE}: {cursor} changes requested by {actor}")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"human-task:changes:{cursor}"}))
        return

    if decision == "reject":
        g["state"] = "rejected"
        gdata["human_task"] = g
        gdata["state"] = "failed"
        lib.dump_yaml(sf, gdata)
        lib.node_outcome(DIR, PID, INSTANCE, task_path, 1, f"rejected by @{actor}")
        lib.publish_check_run(DIR, PID, INSTANCE, proto_data, [], sha)
        body = lib.render_pipeline_status_body(DIR, PID, INSTANCE, PROTO)
        lib.upsert_status_comment(inf, pr, body)
        note = f"⛔ {cursor} rejected by @{actor}. Push a new commit or `/review` to restart."
        if reason:
            note += f"\n\n> {reason}"
        lib.post_pr_comment(pr, note)
        lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "failed")
        lib.cas_push(DIR, f"{INSTANCE}: {cursor} rejected by {actor} → failed")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"human-task:rejected:{cursor}"}))
        return

    refuse(f"Unknown human-task decision '{decision}'.", "human-task: unknown decision")


def _find_open_human_task(proto, want=""):
    """Return the full tree-navigation path to the first open human task, or None.
    Follows LIVE cursors recursively: at each fork branch, read its cursor
    `sub_state`; if it is a human task in state 'open' return its path; if it is a
    nested fork, descend into that fork's child-branch cursors. First open
    human task wins (at most one per branch lineage is open at a time). `want`
    restricts the TOP-level branch only. For a depth-3 human task the returned path is
    byte-identical to the old (branch_id, human_task_id) pair: [fork_id, branch_id, human_task_id].

    Multi-phase cursor awareness (I1 fix): for multi-phase protocols, resolve the
    fork to scan from the _instance.yaml cursor phase, not the first fork in
    the states list. `lib._fork_state` always returns the FIRST fork; in a
    protocol where the cursor is on a LATER fork phase, that would scan the
    wrong branches and find nothing. Mirrors the pattern in join.py main().

    A human task can live under a fork, inside a group, or plainly at the root, and a
    protocol may contain all three. So every strategy is tried IN TURN and the
    first hit wins. An earlier version made the group scan the `else` of the
    fork scan, keyed on `lib._fork_state` — the first fork ANYWHERE. That made a
    grouped question unanswerable in any protocol that merely CONTAINED a fork
    (already joined, long finished), and left a root-level question — no fork,
    no group — with no arm at all."""
    inf = lib.instance_file(DIR, PID, INSTANCE)
    cursor_phase = ""
    if os.path.isfile(inf):
        cursor_phase = lib.load_yaml(inf).get("phase", "") or ""

    # 1. The LIVE cursor phase, whatever kind it is. This is the most specific
    #    signal and the only one that stays right in a multi-phase protocol.
    if cursor_phase:
        res = _scan_scope_for_open_human_task(proto, [cursor_phase], want, top=True)
        if res:
            return res
    # 2. Every other top-level phase, in document order — the cursor may lag
    #    (e.g. a human task opened by a leg while the root cursor names the fork).
    for cand in paths.root_ids(proto):
        if cand == cursor_phase:
            continue
        res = _scan_scope_for_open_human_task(proto, [cand], want, top=True)
        if res:
            return res
    return None


def _scan_scope_for_open_human_task(proto, path, want, top=False):
    """Dispatch one node to the right scan by kind. The kinds that can HOLD an
    open human task are fork (in a leg) and sequence (in the group), and a `question`
    node can BE one. Anything else holds none."""
    kind = paths.node_kind(proto, path)
    if kind == "fork":
        return _scan_fork_for_open_human_task(
            proto, path, paths.node_at_path(proto, path), want, top=top)
    if kind == "sequence":
        return _scan_sequence_for_open_human_task(proto, path, want)
    if kind == "question":
        if want and path[-1] != want:
            return None
        gsf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, path))
        if os.path.isfile(gsf) and \
                lib.load_yaml(gsf).get("human_task", {}).get("state") == "open":
            return list(path)
    return None


def _scan_sequence_for_open_human_task(proto, seq_path, want):
    """Follow a `sequence` group's live cursor looking for an OPEN question.

    A group owns a cursor exactly like a sub-pipeline leg does, so the walk is
    the same: read its `sub_state`, look at that node, and either return it (an
    open question) or descend (a nested group / fork). Without this the scan
    stopped at the group and `/answer` reported "none open" for a question
    that was plainly open — a question inside a group was simply unanswerable."""
    cf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, seq_path))
    if not os.path.isfile(cf):
        return None
    sub = lib.load_yaml(cf).get("sub_state", "")
    if not sub:
        return None
    sub_path = seq_path + [sub]
    if paths.node_kind(proto, sub_path) == "question" and want \
            and sub_path[-1] != want and seq_path[-1] != want:
        # `want` may name the GROUP rather than the question inside it.
        return None
    return _scan_scope_for_open_human_task(proto, sub_path, want)


def _scan_fork_for_open_human_task(proto, fork_path, fo_node, want, top):
    for b in fo_node.get("branches", []):
        bid = b["id"]
        if top and want and bid != want:
            continue
        branch_path = fork_path + [bid]
        cf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto, branch_path))
        if not os.path.isfile(cf):
            continue
        sub = lib.load_yaml(cf).get("sub_state", "")
        if not sub:
            continue  # flat leg (no sub_state) or not yet started
        sub_path = branch_path + [sub]
        # Inside a leg the branch filter has already been applied by `want`
        # above, so the question itself is not re-filtered here (its id is not
        # what `want` names). Delegating keeps one kind-switch: a group's live
        # child may itself be the open question, or another fork, or a question
        # directly.
        res = _scan_scope_for_open_human_task(proto, sub_path, "")
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
    """Parse answer comments, accumulate answers, run coverage check, advance the human task.
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

    human_task_path = _find_open_human_task(proto_data, want)
    if human_task_path is None:
        lib.post_pr_comment(pr, "No open question to answer right now.")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": "answer: none open"}))
        return

    # Derive coords from the human task tree path via path helpers.
    # branch_path is the cursor file's tree path (parent of the human task leaf).
    branch = human_task_path[-2]
    human_task = human_task_path[-1]
    branch_path = human_task_path[:-1]
    # life is the leg's in-flight state value: the enclosing fork id.
    # enclosing_fork_id(["review","B","clarify"]) == "review".
    life = paths.enclosing_fork_id(proto_data, human_task_path)

    # File paths all derived from the human task/branch tree paths via lib.state_path so
    # depth-<=3 filenames stay byte-identical (single-phase drops the leading fork id).
    gsf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto_data, human_task_path))
    gdata = lib.load_yaml(gsf)
    questions = gdata.get("human_task", {}).get("questions", []) or []
    # Interactive (issue-channel) human task: feedback goes to the question issue, not a PR.
    # `fb` is the comment target (the human task's issue if present, else the PR).
    issue_no = gdata.get("human_task", {}).get("issue")
    fb = issue_no or pr

    # Merge new answers into the persisted answers artifact.
    apath = lib.output_artifact_path(DIR, PID, INSTANCE,
                                     path=lib.state_path(proto_data, human_task_path), kind="answers")
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

    # Run the human task's answers-coverage check over the synthesized doc.
    # The check receives FILE PATHS, not answer content — no injection risk.
    # Path-aware (works at any depth): node_at_path resolves the human task node
    # directly. For a depth-3 human task this is the same dict branch_substates returned.
    human_task_cfg = paths.node_at_path(proto_data, human_task_path) or {}
    check_run = (human_task_cfg.get("checks", [{}])[0]).get("run", "answers-coverage")
    pdir = os.path.dirname(os.path.abspath(PROTO))
    res = lib.resolve_executable(f"{pdir}/checks", check_run, pdir, "")
    kind, path = res.split("\t", 1)
    import tempfile
    empty_fd, empty = tempfile.mkstemp(prefix="answers-empty-")
    os.close(empty_fd)
    try:
        # Bounded like every other hook/check subprocess: this one runs in the
        # PLAN job, so a hung coverage check wedges the job holding the state
        # PAT. A timeout is treated as "no verdict" — the same fail-closed
        # answer as unparseable output, so a hang cannot pass a human task.
        cov = _sp.run([path, apath, empty, empty], text=True, capture_output=True,
                      timeout=lib.hook_timeout_seconds())
    except _sp.TimeoutExpired:
        cov = None
        sys.stderr.write(
            f"[next] answers-coverage check timed out after "
            f"{lib.hook_timeout_seconds()}s\n")
    finally:
        os.unlink(empty)   # don't leak the empty diff/files tempfile per answer
    verdict = json.loads(cov.stdout) if cov is not None and cov.stdout.strip() \
        else {"pass": False, "feedback": "no verdict"}

    gdata["human_task"].setdefault("history", []).append(
        {"actor": actor, "answers": list(_parse_answers(body, prefix).keys())})
    if not verdict.get("pass"):
        lib.dump_yaml(gsf, gdata)
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} human task {human_task} partial answers")
        lib.post_pr_comment(fb, f"Recorded. Still needed: {verdict.get('feedback', '')}.")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": "answer: partial"}))
        return

    # Full coverage → close the human task, advance the branch cursor to the next sub-state.
    gdata["human_task"]["state"] = "answered"
    lib.dump_yaml(gsf, gdata)
    # A human task is a STEP of the unit containing it, so it publishes no
    # check-run of its own: record its outcome (answered = nothing objected) and
    # let that unit publish when it completes. Recorded once here so BOTH the
    # nested and the top-level tails below are covered, including their
    # "the human task was the last sub-state" arms.
    lib.node_outcome(DIR, PID, INSTANCE,
                     lib.state_path(proto_data, human_task_path), 0,
                     f"answered by @{actor}")

    # A NESTED human task (enclosing fork is not the top one) advances the enclosing
    # sub-pipeline cursor and re-dispatches protocol-continue carrying the path —
    # next.py's continue-at-NODE_PATH guard then seeds/opens/dispatches the next
    # sibling by kind. The TOP-level path below stays byte-identical (depth-3).
    fork_path = paths.enclosing_fork_path(proto_data, human_task_path) or []
    if len(fork_path) > 1:
        seq_path = paths.parent_path(human_task_path)         # enclosing sequence cursor
        nxt = paths.next_sibling(proto_data, human_task_path)
        sha = gdata.get("head_sha", "") or HEAD_SHA
        cf = lib.state_file(DIR, PID, INSTANCE,
                            path=lib.state_path(proto_data, seq_path))
        cur = lib.load_yaml(cf)
        if nxt:
            cur["sub_state"] = nxt
            cur["state"] = life                          # leg stays in flight
            lib.dump_yaml(cf, cur)
            lib.cas_push(DIR, f"{INSTANCE}: human task {'.'.join(human_task_path)} answered -> {nxt}")
            lib.post_pr_comment(pr, f"{human_task} answered by @{actor}; continuing to {nxt}.")
            lib.dispatch_continue(PID, INSTANCE, path=".".join(seq_path + [nxt]))
        else:
            cur["state"] = "done"                        # the human task was the last sub-state
            lib.dump_yaml(cf, cur)
            lib.cas_push(DIR, f"{INSTANCE}: human task {'.'.join(human_task_path)} answered -> leg done")
            lib.fire_join_dispatch(PID, INSTANCE, fork_path=".".join(fork_path))
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": "answer: complete (nested)"}))
        return

    # Use path.next_sibling directly from human_task_path so the correct enclosing
    # sequence is used regardless of which fork phase the human task lives in.
    # lib.next_substate_id calls _fork_state (first fork) — in a multi-phase
    # protocol with the human task in a NON-first fork phase it would pick the wrong
    # fork and fail to find the sibling. (I1 fix — top-level advance tail.)
    nxt_sub = paths.next_sibling(proto_data, human_task_path)
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
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} human task {human_task} answered -> {nxt_sub}")
        # Interactive human task: close the question issue now that it's fully answered.
        if issue_no:
            lib.close_issue(issue_no, f"Answered by @{actor} — resuming mental-model recovery.")
        lib.post_pr_comment(fb, f"{human_task} answered by @{actor}; continuing to {nxt_sub}.")
        # Path-only dispatch: the unified `continue` handler requires NODE_PATH.
        # nxt_path is the next sub-state's full tree path (e.g. recover.rationale.finalize).
        lib.dispatch_continue(PID, INSTANCE, path=".".join(nxt_path))
    else:
        cur["state"] = "done"
        lib.dump_yaml(cf, cur)
        lib.cas_push(DIR, f"{INSTANCE}: branch {branch} human task {human_task} answered -> leg done")
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

if COMMAND == "resolve-human-task":
    do_resolve_human_task()
    sys.exit(0)

if COMMAND in ("start", "reset"):
    # Unified entry for EVERY protocol shape (single-agent, single-phase fork,
    # multi-phase). enter_root seeds the first top-level node via the recursive
    # sequencer, creates _instance.yaml, applies labels, CAS-pushes, and emits.
    enter_root(COMMAND, HEAD_SHA)
    sys.exit(0)

# A `continue` whose tree path resolves to a fork node dispatches that fork's
# children matrix (nested forks are entered as their own engine invocation). A
# continue MUST carry NODE_PATH — it is the sole coordinate of the unified engine.
if COMMAND == "continue" and NODE_PATH:
    _p = NODE_PATH.split(".")
    _kind = paths.node_kind(proto_data, _p)
    if _kind == "fork":
        # The established seed(emit=False)→cas_push→emit ordering: enter_node seeds
        # the leg files + nested __join.yaml marker locally, cas_push publishes them
        # to origin so the matrix legs (which re-checkout state) find them, THEN emit.
        branches = enter_node(proto_data, _p, "continue")
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter nested fork {NODE_PATH} (continue)")
        if not branches:
            # Empty dynamic-fork short-circuit: a 0-leg materialization would emit
            # run-fork with no legs; every GHA zone guards on legs != '[]' and skips,
            # so the enclosing join never fires and the instance STALLS. Fire THIS
            # fork's join now (a vacuous barrier → all_terminal True → advance to
            # its .next), and emit a noop instead of the empty run-fork. Path-keyed
            # only for a NESTED fork; the top fork stays path-less.
            lib.fire_join_dispatch(PID, INSTANCE, NODE_PATH if len(_p) > 1 else "")
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"empty-fork:{NODE_PATH}"}))
            sys.exit(0)
        print(json.dumps(_fork_action(proto_data, _p, branches)))
        sys.exit(0)
    if _kind == "sequence":
        # A `continue` onto a GROUP: enter it and dispatch its first child.
        # Four engine sites dispatch a continue at a path that can name a group
        # (the group-done arm above, advance.py's kind-agnostic arm, a nested
        # join's `.next`, and do_answer). Without this arm every one of them died
        # at the no-NODE_PATH guard below — exit 2 WITH a NODE_PATH set, so the
        # error named the wrong cause. enter_node already knows how to enter a
        # group; it seeds the cursor and recurses into the first child. Emitting
        # goes through _emit_for_node so the entry emitters stay one function —
        # it is also what dispatches the hand-off for a deterministic first
        # child, and it must run AFTER cas_push (seed→push→emit, as the fork arm
        # does): the state must be on the branch before anything acts on it.
        _seeded = enter_node(proto_data, _p, "continue")
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: enter group {NODE_PATH} (continue)")
        _emit_for_node(_p, _seeded)
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
        # Seeding (iteration-preserve on an in-flight iterate) and action-building
        # (declared inputs + dynamic-fork staged-item re-attachment) are shared
        # with the DISPATCHED `code` arm below (lib.is_dispatched_code) via
        # _seed_or_preserve_leaf / _run_agent_action — both run in the same
        # agent lane, and a second copy of this plumbing is exactly how this
        # file's bugs got in.
        node = paths.node_at_path(proto_data, _p)
        seq = enter_node(proto_data, _p, "continue")
        if seq.get("seeded", True):
            lib.cas_push(DIR, f"{PID}/{INSTANCE}: continue agent {NODE_PATH}")
        print(json.dumps(_run_agent_action(proto_data, _p, node, seq)))
        sys.exit(0)
    if paths.is_human_task(_kind):
        # A `continue` onto a human-task sub-state: enter_node's arm opens the human task
        # (seeds the human task file + check-run + status comment); cas_push publishes.
        enter_node(proto_data, _p, "continue")
        lib.cas_push(DIR, f"{PID}/{INSTANCE}: continue human task {NODE_PATH} open")
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"human-task-open:{NODE_PATH}"}))
        sys.exit(0)
    if _kind == "choice":
        # BPMN Exclusive Gateway. The ONLY node whose successor is data-dependent:
        # read the deciding node's persisted evidence, pull one value out of it,
        # and enter exactly one arm.
        #
        # The whole decision happens HERE, in the plan job — so we emit a `noop`.
        # agentic-engine.yml builds a leg matrix only for run-agent/run-fork, so a
        # noop correctly skips dispatch/checks/advance. Emitting anything else
        # would hand gh-aw a workflow-less leg and run the checks job with zero
        # verdicts (which lib.decide reads as a failed attempt).
        node = paths.node_at_path(proto_data, _p)
        on = node.get("on") or {}
        src_path = paths.parent_path(_p) + [on.get("from", "")]
        ev_file = lib.output_artifact_path(
            DIR, PID, INSTANCE, path=lib.state_path(proto_data, src_path))
        inf = lib.instance_file(DIR, PID, INSTANCE)
        inst = lib.load_yaml(inf) if os.path.isfile(inf) else {}
        pr = lib.pr_from_instance(INSTANCE)

        def _halt(reason, detail):
            # Fail loud + RECOVERABLE. Falling through to the next sibling would
            # look like a successful run that quietly skipped the intended arm.
            # Record the FULL path, not just the leaf id. `/override` resolves
            # what follows the blocked node from this marker, and a bare id is
            # not a coordinate: `next_sibling(proto, [leaf])` cannot address a
            # node inside a group, so a halt there was unrecoverable — defeating
            # the entire reason `on_blocked: halt` exists (letting an operator
            # clear a transient infra failure). `phase` stays for display and
            # for markers written before this field existed.
            inst["halted"] = {"phase": _p[-1], "path": ".".join(_p),
                              "reason": "blocked", "sha": HEAD_SHA}
            lib.dump_yaml(inf, inst)
            # The choice objected; publish the ROOT (the run stops here, so the
            # aggregate gating check-run is what must go red).
            lib.node_outcome(DIR, PID, INSTANCE,
                             lib.state_path(proto_data, _p), 1, detail)
            lib.publish_check_run(DIR, PID, INSTANCE, proto_data, [], HEAD_SHA)
            lib.post_pr_comment(pr, f"⛔ **{_p[-1]}** could not route: {detail}. "
                                    f"A write-access user can comment `/override <reason>`.")
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "blocked")
            lib.cas_push(DIR, f"{INSTANCE}: choice {_p[-1]} unresolved → halted")
            print(json.dumps({"action": "halt", "iteration": 0, "feedback": "",
                              "reason": f"choice-unresolved:{reason}"}))
            sys.exit(0)

        if not os.path.isfile(ev_file):
            _halt("no-evidence",
                  f"`{on.get('from')}` produced no evidence to decide from")
        try:
            with open(ev_file) as _f:
                value = lib.extract_key(json.load(_f), on.get("path", ""))
        except (ValueError, json.JSONDecodeError) as exc:
            _halt("bad-path", f"{on.get('path')} did not resolve: {exc}")

        chosen = None
        for case in node.get("cases") or []:
            if case.get("when") == value:
                chosen = case["next"]
                break
        if chosen is None:
            chosen = node.get("default")
        if chosen is None:
            _halt("no-match",
                  f"{on.get('path')} was {value!r}, which matches no case and "
                  f"the node declares no `default`")

        # Record the decision as this node's own state — an audit trail of WHY
        # the run took this path, and what the deciding value actually was.
        sf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto_data, _p))
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        lib.dump_yaml(sf, {"protocol": PID, "instance": INSTANCE, "state": "done",
                           "decision": {"value": value, "next": chosen},
                           "head_sha": HEAD_SHA, "history": []})

        if chosen in ("done", "failed"):
            fork_path = paths.enclosing_fork_path(proto_data, _p)
            if fork_path is not None:
                # Inside a fork leg, a terminal ends the LEG, not the pipeline —
                # the sibling legs are still running. Mark the leg cursor and
                # fire the barrier, exactly as the code arm does; writing the
                # ROOT cursor here would report the whole run done while the
                # join waits forever.
                leg_path = _p[:-1]
                cf = lib.state_file(DIR, PID, INSTANCE,
                                    path=lib.state_path(proto_data, leg_path))
                cur = lib.load_yaml(cf) if os.path.isfile(cf) else {}
                cur["state"] = "done" if chosen == "done" else "failed"
                os.makedirs(os.path.dirname(cf), exist_ok=True)
                lib.dump_yaml(cf, cur)
                lib.cas_push(DIR, f"{INSTANCE}: choice {'.'.join(_p)} → leg {chosen}")
                lib.fire_join_dispatch(
                    PID, INSTANCE,
                    ".".join(fork_path) if len(fork_path) > 1 else "")
                print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                                  "reason": f"choice:{_p[-1]}:{chosen}"}))
                sys.exit(0)
            inst["phase"] = _p[-1]
            inst["joined"] = True
            lib.dump_yaml(inf, inst)
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, chosen)
            lib.cas_push(DIR, f"{INSTANCE}: choice {_p[-1]} → {chosen}")
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"choice:{_p[-1]}:{chosen}"}))
            sys.exit(0)

        nxt_path = paths.parent_path(_p) + [chosen]
        if len(_p) > 1:
            cf = lib.state_file(DIR, PID, INSTANCE,
                                path=lib.state_path(proto_data, paths.parent_path(_p)))
            cur = lib.load_yaml(cf) if os.path.isfile(cf) else {}
            cur["sub_state"] = chosen
            cur["state"] = paths.enclosing_fork_id(proto_data, _p) or cur.get("state")
            lib.dump_yaml(cf, cur)
        else:
            inst["phase"] = chosen
            lib.dump_yaml(inf, inst)
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, chosen)
        lib.cas_push(DIR, f"{INSTANCE}: choice {_p[-1]} ({value!r}) → {chosen}")
        lib.dispatch_continue(PID, INSTANCE, path=".".join(nxt_path))
        print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                          "reason": f"choice:{_p[-1]}:{chosen}"}))
        sys.exit(0)

    if _kind == "code":
        node = paths.node_at_path(proto_data, _p)
        if lib.is_dispatched_code(node):
            # A `continue` onto a DISPATCHED `code` node (kind:code + workflow:).
            # It runs in the SAME agent lane as an `agent` node — agentic-engine.yml's
            # matrix dispatches it, checks verify its evidence, advance.py records its
            # verdict — so it reuses the agent arm's seed/preserve + inputs-resolution
            # plumbing VERBATIM via the shared helpers, rather than falling into the
            # INLINE `code` handling below (which runs `lib.run_code_hook` synchronously
            # in THIS job — wrong lane for a dispatched node, and would try to resolve a
            # `workflow`-only node as a trusted hook script that doesn't exist).
            seq = _seed_or_preserve_leaf(proto_data, _p, node, "continue")
            if seq.get("seeded", True):
                lib.cas_push(DIR, f"{PID}/{INSTANCE}: continue code {NODE_PATH}")
            print(json.dumps(_run_agent_action(proto_data, _p, node, seq)))
            sys.exit(0)
        # A `continue` onto a MERGE state (INLINE `code`, script:). Two shapes:
        #  - NESTED merge (a per-file `reduce`, path length > 1): LEG-TERMINAL.
        #  - TOP merge (path length 1, dispatched by the top join): finalize instance.
        res = lib.run_code_hook(DIR, PID, INSTANCE, PROTO, node, consuming_path=_p)
        # What does finishing this node complete — a fork LEG, a GROUP, or the
        # RUN? `paths.completing_scope` owns that question for the whole engine.
        # Two earlier spellings were each wrong in one cell: `len(_p) > 1` means
        # "is deep", not "is in a leg" (a top-level group makes depth-2 nodes
        # with no fork above them); and "is there a fork ANYWHERE above" means
        # "is under a fork", not "IS the leg" (a group inside a leg is under a
        # fork, but finishing it ends only the group).
        _scope_kind, _scope_path = paths.completing_scope(proto_data, _p)
        # A GROUP-scope node (never a LEG one — see docs/STATUS.md 4.0.0: a red
        # leg must still reach its join, never dangle mid-flight, so on_blocked
        # is deliberately not consulted for a leg's mid-sequence steps, exactly
        # as before this fix) that objected with on_blocked:"halt" must NOT be
        # handed off to its next sibling — it must fall through to the shared
        # Option-B halt logic further down, unchanged from before this fix (that
        # arm used to be the ONLY thing a mid-group objection could reach, since
        # the group arm did not exist yet). Recomputed here, before the mid-scope
        # arm, so a blocked mid-group node is excluded from it exactly like a
        # blocked group-terminal or root node already is.
        _mid_group_blocked = _scope_kind == "group" and (
            res.get("hook_failed") or (node.get("on_blocked") == "halt" and (
                res.get("blocked") or res.get("exit"))))
        if _scope_kind in ("leg", "group") and paths.next_sibling(proto_data, _p) \
                and not _mid_group_blocked:
            # MID-scope `code` node (mid-LEG or mid-GROUP): the enclosing sequence
            # is NOT over. Persist this step's output (a later sub-state may
            # declare `inputs: [{from: <this node>}]`), advance the enclosing
            # sequence's cursor, and hand off to the next sibling — the same
            # shape advance.py writes for an agent mid-sequence step (`sub_state`
            # moves, `state` stays the enclosing fork id so a leg reads in-flight;
            # for a group with no enclosing fork, `enclosing_fork_id` is None and
            # `state` is left as whatever enter_node seeded it to).
            #
            # Keying the terminal decision on `completing_scope` ALONE was wrong:
            # it answers "leg"/"group" for every node in that scope, mid or last,
            # so a `code` node with a perfectly valid `next` silently truncated the
            # scope at itself — everything after it never ran (a leg's reducer
            # collected a leg whose terminal evidence was missing; a group's
            # successor never dispatched at all). Both LEG and GROUP need the
            # identical fix, and `_scope_path` already equals `parent_path(_p)`
            # for both once the direct-fork-child edge case is excluded (that
            # case has no ordered siblings, so `next_sibling` is already None and
            # never reaches here) — so this one arm covers both, rather than a
            # second, group-only copy of the same plumbing.
            nxt_sub = paths.next_sibling(proto_data, _p)
            ev = lib.output_artifact_path(DIR, PID, INSTANCE,
                                          path=lib.state_path(proto_data, _p))
            os.makedirs(os.path.dirname(ev), exist_ok=True)
            with open(ev, "w") as f:
                json.dump(res, f)
            lib.node_outcome(DIR, PID, INSTANCE, lib.state_path(proto_data, _p),
                             *_code_step_outcome(res))
            cursor_sf = lib.state_file(DIR, PID, INSTANCE,
                                       path=lib.state_path(proto_data, _scope_path))
            cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
            cur["sub_state"] = nxt_sub
            cur["state"] = paths.enclosing_fork_id(proto_data, _p) or cur.get("state")
            os.makedirs(os.path.dirname(cursor_sf), exist_ok=True)
            lib.dump_yaml(cursor_sf, cur)
            # No check-run here: the enclosing LEG or GROUP is the publishing
            # unit, and it publishes when its terminal step ends it. This step's
            # colour is recorded in its outcome and folded in then.
            lib.cas_push(DIR, f"{INSTANCE}: code {'.'.join(_p)} → continue {nxt_sub}")
            lib.dispatch_continue(PID, INSTANCE,
                                  path=".".join(paths.parent_path(_p) + [nxt_sub]))
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"mid-{_scope_kind}-code:{'.'.join(_p)}→{nxt_sub}"}))
            sys.exit(0)
        if _scope_kind == "leg":
            # LEG-TERMINAL `code` node (no `next`), mirroring
            # advance.complete_sequence. (1) persist the merge result as THIS leg's
            # output evidence so the enclosing fork's from_fork can collect the
            # survivors; (2) mark the file-leg SEQUENCE CURSOR done; (3) fire the
            # enclosing fork's join exactly as join.py does (path-less for a
            # top-level enclosing fork, path-keyed if nested).
            leg_path = _scope_path                   # the file-leg sub-pipeline cursor
            ev = lib.output_artifact_path(DIR, PID, INSTANCE, path=lib.state_path(proto_data, _p))
            os.makedirs(os.path.dirname(ev), exist_ok=True)
            with open(ev, "w") as f:
                json.dump(res, f)
            cursor_sf = lib.state_file(DIR, PID, INSTANCE, path=lib.state_path(proto_data, leg_path))
            cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
            cur["state"] = "done"
            lib.dump_yaml(cursor_sf, cur)
            # Record this step, then publish the LEG it terminates — a fork leg
            # is a publishing unit, coloured by the fold of its own steps.
            lib.node_outcome(DIR, PID, INSTANCE, lib.state_path(proto_data, _p),
                             *_code_step_outcome(res))
            lib.publish_check_run(DIR, PID, INSTANCE, proto_data, list(_scope_path),
                                  HEAD_SHA)
            lib.cas_push(DIR, f"{INSTANCE}: nested merge {'.'.join(_p)} → leg done")
            efp = paths.enclosing_fork_path(proto_data, _p)
            fields = {"protocol": PID, "instance": INSTANCE}
            if efp and len(efp) > 1:
                fields["path"] = ".".join(efp)
            lib._gh_dispatch("protocol-join", fields)
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"nested-merge-done:{'.'.join(_p)}"}))
            sys.exit(0)
        # TOP merge — Option-B halt: honor on_blocked / .next before finalizing.
        pr = lib.pr_from_instance(INSTANCE)
        inf = lib.instance_file(DIR, PID, INSTANCE)
        inst = lib.load_yaml(inf) if os.path.isfile(inf) else {}
        # `hook_failed` marks an ENGINE-side failure to obtain a verdict at all:
        # the hook was unresolved, exited nonzero, or printed unparseable stdout
        # (lib.run_code_hook). That ALWAYS halts, whether or not the node opted
        # into on_blocked:halt — `on_blocked` is an opt-in for a hook that
        # DELIBERATELY returns blocked:true, and a node should not have to opt in
        # to "don't pretend I succeeded". Falling through to .next would run the
        # successor on data this step never computed and report the run ✅ done.
        # (The terminal arm below already fails closed; this makes mid-pipeline
        # agree.)
        #
        # A hook that RAN and chose `neutral` is different — it reported "nothing
        # to do" (e.g. recover's combine when no leg produced a tree), which is a
        # real verdict and must not halt. Hence the explicit flag rather than
        # treating every neutral as a crash.
        #
        # A GENUINE `failure` conclusion stays gated on on_blocked:halt: a reducer
        # may legitimately report failure and still want the pipeline to continue
        # to a node that handles it. 4.0.0: the retired {conclusion,summary}
        # envelope is no longer part of this decision at all -- a hook's verdict
        # only halts a `halt`-declaring node via the ONE signal an exit code
        # gives (`exit`, nonzero) or an explicit `blocked` flag. A production
        # reducer that wants a 'failure' conclusion to halt sets `blocked` to
        # say so explicitly (e.g. code-review's aggregate-honesty always keys
        # `blocked` off the same condition as `conclusion`).
        if res.get("hook_failed") or (node.get("on_blocked") == "halt" and (
                res.get("blocked") or res.get("exit"))):
            # A required node returned blocked:true (or a non-genuine/crashed verdict)
            # → HALT before .next. Record the `halted` marker do_override reads
            # (advance.py:728 shape), fail the check-run, label 'blocked', and stop.
            # `/override` clears it and continues to next_sibling(honesty-verdict) == post-fix.
            # Record the FULL path, not just the leaf id. `/override` resolves
            # what follows the blocked node from this marker, and a bare id is
            # not a coordinate: `next_sibling(proto, [leaf])` cannot address a
            # node inside a group, so a halt there was unrecoverable — defeating
            # the entire reason `on_blocked: halt` exists (letting an operator
            # clear a transient infra failure). `phase` stays for display and
            # for markers written before this field existed.
            inst["halted"] = {"phase": _p[-1], "path": ".".join(_p),
                              "reason": "blocked", "sha": HEAD_SHA}
            lib.dump_yaml(inf, inst)
            gsum = res.get("summary", "") or "a required check did not pass"
            # Record the objecting step, then publish the ROOT: the run halts
            # here, so the aggregate gating check-run is what must go red.
            lib.node_outcome(DIR, PID, INSTANCE, lib.state_path(proto_data, _p),
                             res.get("exit", 1) or 1, gsum)
            lib.publish_check_run(DIR, PID, INSTANCE, proto_data, [], HEAD_SHA)
            lib.post_pr_comment(pr, f"⛔ **{_p[-1]}** blocked: {gsum}. "
                                    f"A write-access user can comment `/override <reason>` to proceed.")
            lib.upsert_status_comment(inf, pr, lib.render_instance_status_body(DIR, PID, INSTANCE, PROTO))
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, "blocked")
            lib.cas_push(DIR, f"{INSTANCE}: merge {_p[-1]} blocked → pipeline halted")
            print(json.dumps({"action": "halt", "iteration": 0, "feedback": "",
                              "reason": f"merge-blocked:{_p[-1]}"}))
            sys.exit(0)
        if node.get("next") and lib.state_by_id(proto_data, node["next"]):
            # Not blocked and the node names a REAL successor → CONTINUE past the
            # merge instead of finalizing (multi-phase after a top merge, e.g.
            # post-fix).
            #
            # `state_by_id` is the guard, not the bare key: `done` is a SENTINEL,
            # not a node, and `next: "done"` means "nothing follows" — exactly what
            # join.py has always checked (`if nxt and lib.state_by_id(...)`).
            # Without it recover-mental-model{,-interactive}'s `combine` took this
            # arm, which publishes nothing because the run is supposedly unfinished,
            # and then dispatched a continue onto a path that resolves to no node.
            # Those protocols have no PR and nothing downstream, so their aggregate
            # check-run — opened `in_progress` by agentic-engine.yml — never
            # completed at all.
            nxt = node["next"]
            # Persist the merge rollup as THIS node's output evidence so a
            # downstream `{from:'<this-node>'}` agent input resolves (e.g. mrp's
            # {from:'honesty-verdict', as:'honesty'} → aggregate-honesty's
            # {conclusion,summary,blocked,rollup}). Mirrors the nested-merge arm above;
            # the path is state_path(proto, _p) — byte-identical to what resolve_inputs
            # computes for a top-level `from` — and is written BEFORE cas_push so it
            # lands on the state branch with the rest of this transition.
            ev = lib.output_artifact_path(DIR, PID, INSTANCE, path=lib.state_path(proto_data, _p))
            os.makedirs(os.path.dirname(ev), exist_ok=True)
            with open(ev, "w") as f:
                json.dump(res, f)
            concl, summary, _label = lib.finalize_code_result(res)
            inst["phase"] = nxt
            lib.dump_yaml(inf, inst)
            # Mid-pipeline: record this step only. The root sequence has NOT
            # finished (`nxt` still runs), so nothing publishes here — completing
            # the aggregate now would green the merge box on unfinished work.
            lib.node_outcome(DIR, PID, INSTANCE, lib.state_path(proto_data, _p),
                             *_code_step_outcome(res))
            lib.post_pr_comment(pr, f"🧬 **{_p[-1]}**: {summary}")
            lib.upsert_status_comment(inf, pr, lib.render_instance_status_body(DIR, PID, INSTANCE, PROTO))
            lib.ensure_phase_label(DIR, PID, INSTANCE, proto_data, pr, nxt)
            lib.cas_push(DIR, f"{INSTANCE}: merge {_p[-1]} clear → continue {nxt}")
            lib.dispatch_continue(PID, INSTANCE, path=nxt)
            print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                              "reason": f"merge-continue:{nxt}"}))
            sys.exit(0)
        # A code node ENDING A GROUP (no `.next`, and — since the mid-scope arm
        # above already returned for anything with a `next_sibling` — no sibling
        # either) means the GROUP finished — not the run. Continue at the
        # group's own successor. Falling into the finalize arm below reported
        # the whole instance done and silently skipped everything after the
        # group.
        if _scope_kind == "group":
            group_path = _scope_path
            nxt_after = paths.next_sibling(proto_data, group_path)
            ev = lib.output_artifact_path(DIR, PID, INSTANCE,
                                          path=lib.state_path(proto_data, _p))
            os.makedirs(os.path.dirname(ev), exist_ok=True)
            with open(ev, "w") as _f:
                json.dump(res, _f)
            cf = lib.state_file(DIR, PID, INSTANCE,
                                path=lib.state_path(proto_data, group_path))
            cur = lib.load_yaml(cf) if os.path.isfile(cf) else {}
            cur["state"] = "done"
            os.makedirs(os.path.dirname(cf), exist_ok=True)
            lib.dump_yaml(cf, cur)
            # Record this step; the GROUP it terminates is a sequence, so it is a
            # publishing unit and publishes its one check-run here.
            lib.node_outcome(DIR, PID, INSTANCE, lib.state_path(proto_data, _p),
                             *_code_step_outcome(res))
            lib.publish_check_run(DIR, PID, INSTANCE, proto_data, list(group_path),
                                  HEAD_SHA)
            if nxt_after:
                nxt_path = paths.parent_path(group_path) + [nxt_after]
                if len(nxt_path) == 1:
                    inst["phase"] = nxt_after
                    lib.dump_yaml(inf, inst)
                lib.cas_push(DIR, f"{INSTANCE}: code {'.'.join(_p)} → group done, "
                                  f"continue {nxt_after}")
                lib.dispatch_continue(PID, INSTANCE, path=".".join(nxt_path))
                print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                                  "reason": f"group-done:{'.'.join(group_path)}"}))
                sys.exit(0)
            # Nothing follows the group INSIDE its own scope, so finishing it
            # finishes that scope too — which may finish ITS scope, and so on.
            # Bubble up to the outermost scope actually completed. A group that
            # is last in a fork LEG ends the leg (fire the join); a group last at
            # the ROOT ends the run (fall through to finalize).
            _outer_kind, _outer_path = _scope_kind, group_path
            while True:
                _k, _s = paths.completing_scope(proto_data, _outer_path)
                if _k == "leg":
                    _outer_kind, _outer_path = "leg", _s
                    break
                if _k == "root" or paths.next_sibling(proto_data, _outer_path):
                    _outer_kind = _k
                    break
                # This scope is also last in ITS scope → keep bubbling.
                _outer_path = _s
            if _outer_kind == "leg":
                # A group ending a LEG: mark the leg's cursor done and fire the
                # enclosing fork's join, exactly as the leg-terminal arm above.
                lcf = lib.state_file(DIR, PID, INSTANCE,
                                     path=lib.state_path(proto_data, _outer_path))
                lcur = lib.load_yaml(lcf) if os.path.isfile(lcf) else {}
                lcur["state"] = "done"
                os.makedirs(os.path.dirname(lcf), exist_ok=True)
                lib.dump_yaml(lcf, lcur)
                # The bubbling ended a fork LEG: publish it (the group above
                # already published its own).
                lib.publish_check_run(DIR, PID, INSTANCE, proto_data,
                                      list(_outer_path), HEAD_SHA)
                lib.cas_push(DIR, f"{INSTANCE}: code {'.'.join(_p)} → group done "
                                  f"→ leg {'.'.join(_outer_path)} done")
                efp = paths.enclosing_fork_path(proto_data, _outer_path)
                fields = {"protocol": PID, "instance": INSTANCE}
                if efp and len(efp) > 1:
                    fields["path"] = ".".join(efp)
                lib._gh_dispatch("protocol-join", fields)
                print(json.dumps({"action": "noop", "iteration": 0, "feedback": "",
                                  "reason": f"group-done-leg:{'.'.join(_outer_path)}"}))
                sys.exit(0)
            # The group was last at the ROOT: nothing follows it, so the run
            # really is done — fall through to the finalize arm.
            lib.cas_push(DIR, f"{INSTANCE}: code {'.'.join(_p)} → group done (last)")

        # TOP merge (no .next): finalize the instance (existing behavior).
        inst["phase"] = _p[-1]
        inst["joined"] = True
        lib.dump_yaml(inf, inst)
        concl, summary, label = lib.finalize_code_result(res)
        # FINALIZE: record this last step, then publish the ROOT sequence — the
        # aggregate gating check-run, coloured by the fold of every step in the run.
        lib.node_outcome(DIR, PID, INSTANCE, lib.state_path(proto_data, _p),
                         *_code_step_outcome(res))
        lib.publish_check_run(DIR, PID, INSTANCE, proto_data, [], HEAD_SHA)
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

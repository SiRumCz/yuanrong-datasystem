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
import typing

# Import shared library from the same directory as this script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
import paths as _paths


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
    max_iter: typing.Any
    github_repository: str
    sha: str
    life_state: typing.Any
    tree_path: typing.Optional[list]
    file_path: typing.Optional[list]
    proto: dict


def _conclude_unless_dispatched(proto_path, proto, evid, instance, dir_, tree_path):
    """Run the node's `conclude` hook — UNLESS the node is a dispatched `code`
    node (`kind: "code"` + `workflow:`, per `lib.is_dispatched_code`).

    Task 1's validator forbids the `conclude` key on a dispatched `code` node
    (a real exit code needs no proxy verdict), so `lib.run_conclude_hook`
    would already resolve nothing and return `None` (neutral) for one —
    relying on that omission is fragile, though: it would silently do the
    wrong thing the moment validation is bypassed (a hand-edited state
    branch, a future relaxation of the schema) rather than failing loud.
    Skip explicitly instead: a dispatched node's own exit status — folded
    into `blocking` via the checks job's synthetic `dispatched-run` verdict
    (Task 3) — IS its verdict; there is nothing for a second hook to add.

    Used at every site in this file that used to call `run_conclude_hook`
    unconditionally, now that a dispatched `code` node's LAST sub-pipeline
    step (Task 4's fork-leg/mid-sequence shapes) can reach them too — not
    just the two root-child (depth-1) sites this task's plan names."""
    node = _paths.node_at_path(proto, tree_path)
    if lib.is_dispatched_code(node or {}):
        return None
    return lib.run_conclude_hook(proto_path, proto, evid, instance,
                                 dir_=dir_, tree_path=tree_path)


def _is_agent_lane_root_phase(proto, tree_path):
    """True iff `tree_path` names a ROOT-CHILD phase running in the AGENT
    LANE: an `agent` node, or a `code` node DISPATCHED as a workflow
    (`lib.is_dispatched_code`) — both are seeded/dispatched/advanced
    identically (Task 4), so both must reach the depth-1 clear/exhaust
    guards below (root-cursor advance, phase labels, halt-on-block).

    Before Task 5 this was spelled `node_kind(...) == "agent"` — a POSITION
    concern ("is this a top-level phase reached via the agent lane?")
    mis-spelled as a KIND concern. The bug that mis-spelling caused: a
    root-level dispatched `code` phase (e.g. this plan's `gather`) would
    fall through to the KIND-AGNOSTIC 'remaining done case' further down,
    which publishes a check-run and a status comment but never advances
    `instance['phase']` or dispatches the next `protocol-continue` — the
    pipeline would complete the phase and then STALL forever, silently,
    with a green check-run lying about progress. See
    test_dispatched_code_advance.py, which fails on the unwidened guard."""
    return _paths.is_root_child(proto, tree_path) and (
        _paths.node_kind(proto, tree_path) == "agent"
        or lib.is_dispatched_code(_paths.node_at_path(proto, tree_path) or {}))


def _join_path(proto, tree_path):
    """Dot-joined path of the ENCLOSING fork, but ONLY when it is NESTED
    (tree path length > 1); else "". Carried as fire_join's client_payload[path]
    so join.py evaluates the right barrier. The TOP fork (length 1) and the
    legacy depth-<=3 path (tree_path is None) both yield "" → a path-less join,
    byte-identical to the legacy behavior."""
    if tree_path is None or proto is None:
        return ""
    import paths as _paths
    fp = _paths.enclosing_fork_path(proto, tree_path)
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
    """Run 'gh api ...' with ENGINE_LOCAL short-circuit and token-pool rotation.
    Delegates to lib.run_gh_rotating so a rate-limited dispatch token fails over
    to the next token in the pool. If rotation still can't land the call (None or
    nonzero) fail LOUD (advance.py is a script): print the replay `gh api` command
    and exit nonzero so the advance job fails red instead of stalling silently."""
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] gh api {' '.join(args)}\n")
        return
    result = lib.run_gh_rotating(list(args))
    if result is None or result.returncode != 0:
        err = result.stderr if result is not None else "no result"
        sys.stderr.write(f"[engine] gh api failed after token rotation: {err}; "
                         f"recover by re-firing: gh api {' '.join(args)}\n")
        sys.exit(1)


def fire_join(pid, instance, branch, fork_path=""):
    """On a TERMINAL branch (done OR failed), signal the fan-out barrier.
    No-op for the single-agent path (branch empty).

    `fork_path` (dot-joined TREE path of the ENCLOSING fork) is carried as
    client_payload[path] ONLY for a NESTED fork (path length > 1); join.py
    (Task 12b) reads it to evaluate the right nested barrier. For the TOP fork
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
    if fork_path:
        args += ["-F", f"client_payload[path]={fork_path}"]
    gh_api(*args)


def complete_sequence(ctx, cur):
    """Terminal action for the last sub-state of a done sub-pipeline leg.
    Marks the leg cursor done, emits a status comment, CAS-pushes, and fires join.
    Called from advance_node when the last sub-state of branch finishes successfully.

    When the enclosing fork is NESTED (path length > 1) the join dispatch carries
    its path so join.py evaluates the right barrier; the TOP fork (length 1) fires
    a path-less join — byte-identical to the legacy behavior.

    A sequence is only leg-terminal when it IS a fork leg. A top-level `sequence`
    group is not: finishing its last child means the GROUP is done, and the run
    continues at the group's next sibling. Firing a join there would wait on a
    barrier for a fork that does not exist."""
    cur["state"] = "done"             # last sub-state → leg terminal
    lib.dump_yaml(ctx.cursor_sf, cur)
    # The enclosing sequence (a fork LEG, or a top-level GROUP — both are
    # publishing units) has just finished: publish ITS one check-run, coloured by
    # the fold of its steps. This is the only place a sub-pipeline leg's colour
    # is emitted; the steps themselves only record node_outcome.
    if ctx.tree_path is not None and ctx.proto is not None:
        lib.publish_check_run(ctx.dir_, ctx.pid, ctx.instance, ctx.proto,
                              _paths.parent_path(ctx.tree_path), ctx.sha)
    update_status_comment(ctx.sf, ctx.inf, ctx.branch, ctx.pr, ctx.pid, ctx.instance,
                          ctx.proto_path, ctx.dir_, "✅ done — published.",
                          ctx.max_iter, ctx.github_repository)
    lib.cas_push(ctx.dir_, f"{ctx.instance}: branch {ctx.branch} {ctx.substate} done → leg done")
    # Is the finished scope a GROUP or a fork LEG? Ask what the enclosing scope's
    # PARENT is, not whether a fork exists anywhere above: a group nested INSIDE
    # a leg has an enclosing fork, but finishing it still means the GROUP ended,
    # not the leg. Keying on "no fork anywhere" skipped everything after such a
    # group and marked the group's cursor as the leg's.
    _scope = _paths.parent_path(ctx.tree_path) if ctx.tree_path else []
    _scope_is_group = bool(_scope) and (
        len(_scope) == 1
        or _paths.node_kind(ctx.proto, _paths.parent_path(_scope)) != "fork")
    if ctx.tree_path is not None and ctx.proto is not None and _scope_is_group \
            and _paths.node_kind(ctx.proto, _scope) == "sequence":
        # A GROUP finished, not a fork leg: continue at the group's successor.
        group_path = _paths.parent_path(ctx.tree_path)
        nxt = _paths.next_sibling(ctx.proto, group_path) if group_path else None
        if nxt:
            nxt_path = _paths.parent_path(group_path) + [nxt]
            lib.dispatch_continue(ctx.pid, ctx.instance, path=".".join(nxt_path))
        return
    fire_join(ctx.pid, ctx.instance, ctx.branch, _join_path(ctx.proto, ctx.tree_path))


def advance_node(ctx, process, outcome=(0, "")):
    """Advance a sub-pipeline branch node.  Called when ``ctx.branch`` and
    ``ctx.substate`` are both set.

    process=='done':   If next sibling exists → seed/dispatch it (agent), open the
                       human task (approval/question kind), or — when the next sibling is a FANOUT —
                       re-dispatch protocol-continue with client_payload[path]=<fork
                       tree path> (so next.py's `continue` enters the nested fork)
                       WITHOUT seeding a leg file; else → complete_sequence (leg terminal).
    process=='failed': Mark the branch cursor failed so the join barrier can observe
                       the leg's outcome; the caller (main) handles the shared
                       check-run / status-comment / cas-push / fire-join.

    `ctx.tree_path` (NODE_PATH mode) is the canonical TREE path of the leaf being
    advanced (e.g. ["preflight","deep","triage"]). When set, sibling lookup +
    file naming route through paths.* / lib.state_path so depth-4 works; when None
    the legacy depth-<=3 branch/phase/substate behavior is byte-identical.

    `outcome` is THIS step's (exit, summary) — recorded before any successor is
    entered, because a leg-terminal sub-state hands straight to complete_sequence,
    which FOLDS the leg's steps to colour its check-run. Recording after would
    publish the leg from a fold that had not yet seen its last step. The default
    (0, "") is the "nothing objected" caller (an auto-resolved human task)."""
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
    # Mark this sub-state's own file done (already set above), record ITS exit
    # status (the unit that encloses it publishes the check-run), then move on.
    lib.node_outcome(dir_, pid, instance, lib.state_path(proto, tree_path),
                     outcome[0], outcome[1])
    cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
    if nxt_sub:
        nxt_kind = _paths.node_kind(proto, parent + [nxt_sub])
        nxt_state = None

        # --- Next sibling is a FANOUT → enter it via protocol-continue. ---
        # The leg stays in-flight; we move the cursor onto the fork id and let
        # next.py's `continue` (NODE_PATH=<fork path>) seed the fork's child
        # legs + nested __join.yaml. We deliberately do NOT seed a leg file here.
        if nxt_kind == "fork":
            cur["sub_state"] = nxt_sub
            cur["state"] = life_state         # leg stays in flight
            lib.dump_yaml(cursor_sf, cur)
            fork_tree_path = parent + [nxt_sub]
            lib.cas_push(dir_, f"{instance}: {'.'.join(tree_path)} done → fork {nxt_sub}")
            gh_api(
                f"repos/{github_repository}/dispatches",
                "-f", "event_type=protocol-continue",
                "-F", f"client_payload[protocol]={pid}",
                "-F", f"client_payload[instance]={instance}",
                "-F", f"client_payload[path]={'.'.join(fork_tree_path)}",
            )
            return

        cur["sub_state"] = nxt_sub
        cur["state"] = life_state         # leg stays in flight
        lib.dump_yaml(cursor_sf, cur)
        if _paths.is_human_task(nxt_kind):
            # Open the human task; read questions from the source sub-state's persisted
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
                # Auto-complete an empty `question` node ONLY when the source agent deliberately
                # surfaced an EXPLICIT empty `questions` list — advance past it as if
                # resolved (→ the node's next, or leg-terminal → join). A missing / null / garbled
                # `questions` is an agent malfunction, not a decision to skip a HUMAN task, so
                # fall through to open_human_task (fail-closed: hold for a human) instead of silently
                # advancing. (The cursor sub_state was already set to the human task above.)
                human_task_path = parent + [nxt_sub]
                gsf = lib.state_file(dir_, pid, instance,
                                     path=lib.state_path(proto, human_task_path))
                lib.dump_yaml(gsf, {"protocol": pid, "instance": instance,
                                    "state": "done", "head_sha": sha,
                                    "human_task": {"state": "auto-resolved", "history": []}})
                advance_node(dataclasses.replace(
                    ctx, substate=nxt_sub, tree_path=human_task_path), "done")
                return
            human_task_channel = (_paths.node_at_path(proto, parent + [nxt_sub]) or {}).get("channel", "comment")
            lib.open_human_task(dir_, pid, instance, proto_path, nxt_sub, sha, pr,
                          questions=questions,
                          path=lib.state_path(proto, parent + [nxt_sub]),
                          tree_path=parent + [nxt_sub],
                          channel=human_task_channel)
            lib.cas_push(dir_, f"{instance}: branch {branch} {substate} done → human task {nxt_sub} open")
            return
        # Otherwise: any node entered via a path-continue — an agent sub-state
        # today, and equally a `code`/`choice`/`sequence` node. This arm is
        # deliberately kind-AGNOSTIC: it moves the cursor and hands off to
        # next.py's `continue`, whose dispatcher owns the per-kind behavior (and
        # fails loud on a kind it does not implement). So a new kind needs no arm
        # here — only in next.py.
        #
        # Advance the cursor (done above) and
        # dispatch a path-continue; the continue's enter_node SEEDS the sub-state
        # file. We deliberately do NOT pre-seed it here — pre-seeding makes the
        # follow-on continue's cas_push an empty commit (identical content), which
        # aborts an agent→agent sub-pipeline transition. Mirrors do_answer's
        # human-task→next-substate handling in next.py (which also leaves seeding to the
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


# Redaction now lives in lib.py: 4.0.0 routes every check-run through
# lib.publish_check_run, whose summary text is a hook's own stdout, so BOTH hook
# kinds reach a public surface and must be scrubbed by the same function. The
# aliases keep advance.py's internal callers (and its tests) working unchanged.
_TOKEN_PATTERNS = lib._TOKEN_PATTERNS
_SECRET_ENV_VARS = lib._SECRET_ENV_VARS
_redact = lib._redact


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
    proto = lib.load_protocol(proto_path)
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
        body = lib.render_fork_status_body(dir_, pid, instance, proto_path)
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
    proto = lib.load_protocol(proto_path)

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
    # Surface branch/substate so the `if branch and substate:` sub-pipeline arm is selected
    # in main() fire; these are the leg's immediate parent + own ids (advance_node
    # uses tree_path for real navigation).
    branch = tree_path[-2] if len(tree_path) >= 2 else ""
    substate = tree_path[-1]
    phase = ""
    file_path = lib.state_path(proto, tree_path)
    sf = lib.state_file(dir_, pid, instance, path=file_path)

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
            "human_task": {},
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
    # "Were checks declared for this node at all?" is distinct from "did they
    # produce results" -- a node with no checks[] legitimately has none of
    # either, and must not be treated as a checks-job failure (lib.decide).
    # Default True when the key is absent (e.g. the synthetic fallback verdict
    # agentic-engine.yml writes when the checks job itself dies) so that path's
    # existing failed/iterate behavior is unchanged.
    checks_declared = verdicts.get("checks_declared", True)
    # DECIDE: the process axis (iterate/done/failed) is a pure fold over the
    # verdicts + their on_fail severities. `blocking` (a block-severity fail)
    # is consumed below via lib.block_exit, folded with each arm's own
    # `conclude` hook result into the node's exit code.
    process, blocking = lib.decide(
        results, iterations_remaining=(iter_ < max_iter), checks_declared=checks_declared,
    )

    # Feedback fed back to the agent: only iterate-severity failures, since the
    # agent cannot fix advisory/block facts by re-running. Defaulting on_fail to
    # "iterate" keeps the single-agent regression path byte-identical (all v1
    # checks are iterate-severity, so this is every non-pass verdict).
    fb_parts = [r.get("feedback", "") for r in results
                if not r.get("pass", False) and r.get("on_fail", "iterate") == "iterate"]
    fb = "; ".join(p for p in fb_parts if p)
    if not fb and len(results) == 0 and checks_declared:
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
                 proto_path=proto_path, max_iter=max_iter,
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

        # --- FLAT nested-fork child leg (NODE_PATH, parent is a FANOUT). ---
        # Its parent is a fork, NOT a sub-pipeline sequence, so there is no
        # leg-cursor to advance: the leg is its OWN terminal (tracked by the
        # fork's per-leg files + __join.yaml). Mark this leg's own sf done and
        # fire the enclosing fork's path-keyed join — DO NOT write a cursor
        # file at the parent (that would prematurely mark the whole fork done).
        if _paths.is_fork(proto, _paths.parent_path(tree_path)):
            # THE HOLE THIS CLOSES: a FLAT fork leg never ran its `conclude`, so
            # the six `on_fail: "block"` declarations in recover-mental-model{,
            # -interactive} (legion / codeset / ubiquitous-language) could not
            # reach a check-run at all. Run it here exactly as the root-child and
            # sub-pipeline arms do, and fold it with the ENGINE's `blocking`.
            #
            # COLOUR IS NOT FLOW: a nonzero exit paints this leg RED and the run
            # still fires the join and continues. Only a node declaring
            # `on_blocked: "halt"` stops — which is what lets code-review's five
            # `review` legs go red on findings and still reach the `per-issue`
            # phase that fixes them.
            #
            # (Presently unreachable by a DISPATCHED code leaf, and equally by
            # an agent one: `lib.normalize_protocol` wraps every fork branch —
            # `agent` and `code` alike — into a one-child `sequence` at load,
            # so the parent of any dispatched LEAF is that `sequence`, never
            # the `fork` itself; a flat fork child is fixture/test-only. Guard
            # kept defensively — `_conclude_unless_dispatched` costs nothing
            # extra here and this arm should not silently rot if that
            # normalization invariant ever changes.)
            _cc = _conclude_unless_dispatched(proto_path, proto, evid, instance, dir_, tree_path)
            _exit, _sum = lib.block_exit(blocking, _cc)
            lib.node_outcome(dir_, pid, instance, lib.state_path(proto, tree_path),
                             _exit, _sum)
            lib.publish_check_run(dir_, pid, instance, proto, list(tree_path), sha)
            update_status_comment(sf, inf, branch, pr, pid, instance, proto_path, dir_,
                                  "✅ done — published.", max_iter, github_repository)
            lib.cas_push(dir_, f"{instance}: {'.'.join(tree_path)} done → leg done")
            fire_join(pid, instance, branch, _join_path(proto, tree_path))
            return

        # --- Sub-pipeline branch leg: advance the BRANCH CURSOR, not the phase. ---
        if branch and substate:
            # A nested leg state may declare its own `conclude` hook (per-issue
            # triage/fix). The root-child conclude block below is reached ONLY by
            # depth-1 phases, so without this a nested conclude (conclude-triage /
            # conclude-fix — the per-leg commit/push/close) would NEVER fire. Run it
            # BEFORE advancing, mirroring the root-child block. Nested legs are not
            # human tasks (no on_blocked), so there is no halt arm — a failed conclude
            # still advances, but must surface as a RED step, which colours the
            # enclosing leg's check-run when complete_sequence folds it.
            # DELIBERATE (not an oversight): this stays true for a HOOK_FAILED_EXIT
            # conclude too, same as any other nonzero exit here. Adding a halt at
            # this position would need a branch-scoped `halted`/override story that
            # does not exist yet (unlike the root-child arm, which already has one)
            # — that is a structural addition, not this task's ABI-only scope,
            # and code-review's per-issue legs rely on "red still advances the leg
            # cursor, the join arbitrates" exactly as they do for an ordinary
            # objection: a leg that goes red must still reach the phase that
            # inspects/fixes it, never dangle joinless.
            #
            # A DISPATCHED code node's LAST sub-pipeline/fork-leg step (e.g.
            # this plan's `sec` leg) reaches this arm too — a genuinely NEW
            # path Task 4 opened (inline `code` never reached advance.py at
            # all). `conclude` is forbidden on it (Task 1), so skip it
            # explicitly rather than trust the empty-`conclude` no-op.
            _cc = _conclude_unless_dispatched(proto_path, proto, evid, instance, dir_, tree_path)
            _exit, _sum = lib.block_exit(blocking, _cc)
            ctx.cursor_sf = lib.state_file(
                dir_, pid, instance,
                path=lib.state_path(proto, _paths.parent_path(tree_path)))
            # The step's outcome rides INTO advance_node so it is recorded before
            # a leg-terminal sub-state hands to complete_sequence, which folds the
            # leg's steps to colour the leg's one check-run.
            advance_node(ctx, process="done", outcome=(_exit, _sum))
            return

        # --- Depth-1 AGENT-LANE phase (root child) clear tail. ---
        # When the node is a root-level agent phase (e.g. code-review's
        # `preflight`) OR a root-level DISPATCHED `code` phase (e.g. this
        # plan's `gather`) — see `_is_agent_lane_root_phase` for why both
        # belong here — advance the root cursor via path-continue.
        if _is_agent_lane_root_phase(proto, tree_path):
            _this_state = lib.state_by_id(proto, agent_state)
            # `conclude` is forbidden on a dispatched `code` node (Task 1);
            # skip the hook explicitly rather than trust the empty-key no-op.
            _cc = _conclude_unless_dispatched(proto_path, proto, evid, instance, dir_, tree_path)
            _exit, _sum = lib.block_exit(blocking, _cc)
            # THIS STEP's exit status. The conclude hook + `blocking` are the
            # ONLY signal (5.0.0): `publish` is retired, so a node with neither
            # a `conclude` nor a blocking check has no verdict source and stays
            # clear. `_exit` itself is untouched — it alone decides HALT.
            _node_exit = _exit
            csum = _sum
            _phase_id = tree_path[-1]
            # A nonzero exit is the OBJECTION -- from a failed block-severity
            # check, the conclude hook, or both (lib.block_exit folds them to
            # the worst). An ordinary objection halts the pipeline only when
            # the node itself declares on_blocked:"halt" -- but HOOK_FAILED_EXIT
            # is not an ordinary objection: it means the hook produced NO
            # verdict at all (unresolved/not-exec/timed out), so there is
            # nothing for on_blocked to opt into. Mirrors next.py's
            # `res.get("hook_failed")` unconditional-halt clause for `code`
            # hooks: the absence of a verdict is never read as consent to
            # advance, whether or not the node declared on_blocked.
            if _exit == lib.HOOK_FAILED_EXIT or (
                    _exit and (_this_state or {}).get("on_blocked") == "halt"):
                # BLOCKED → terminate the pipeline before the next phase.
                state_data = lib.load_yaml(sf)
                state_data["state"] = "failed"
                lib.dump_yaml(sf, state_data)
                # Record the objecting step, then publish the unit that contains
                # it. A root-child phase is a STEP of the root sequence, so the
                # unit IS the root — the aggregate gating check-run, which is
                # exactly what must go red when the pipeline halts.
                lib.node_outcome(dir_, pid, instance, lib.state_path(proto, tree_path),
                                 _node_exit,
                                 csum or "A required check did not pass; pipeline halted.")
                lib.publish_check_run(dir_, pid, instance, proto,
                                      list(_paths.completing_scope(proto, tree_path)[1]), sha)
                inst_data = lib.load_yaml(inf) if os.path.isfile(inf) else {}
                # Full path, not just the leaf id — `/override` resolves the
                # successor from this marker and a bare id cannot address a node
                # inside a group. See next.py's matching writers.
                inst_data["halted"] = {"phase": _phase_id, "path": ".".join(tree_path),
                                       "reason": "blocked", "sha": sha}
                lib.dump_yaml(inf, inst_data)
                update_status_comment(
                    sf, inf, branch, pr, pid, instance, proto_path, dir_,
                    "⛔ blocked", max_iter, github_repository
                )
                notice = (f"⛔ **{_phase_id}** blocked: "
                          f"{csum or 'a required check did not pass'}. "
                          f"A write-access user can comment `/override <reason>` "
                          f"to proceed past it.")
                lib.post_pr_comment(pr, notice)
                lib.ensure_phase_label(dir_, pid, instance, proto, pr, "blocked")
                lib.cas_push(dir_, f"{instance}: phase {_phase_id} blocked → pipeline halted")
            else:
                # CLEAR → advance root cursor via path-continue.
                # `concl` is never "blocked" here: a blocked conclude goes to
                # the halt arm above; this arm only runs on a clear verdict.
                nxt = _paths.next_sibling(proto, tree_path)
                # A crashed/failed conclude hook still ADVANCES — halt requires
                # on_blocked:"halt", which it never sets — but must be surfaced
                # as a RED step, which reddens the unit containing it when that
                # unit publishes. COLOUR IS NOT FLOW.
                lib.node_outcome(dir_, pid, instance, lib.state_path(proto, tree_path),
                                 _node_exit, csum)
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
                    # No further sibling → the ROOT sequence finished: publish the
                    # aggregate, coloured by the fold of every step under it (so a
                    # final phase whose conclude crashed still lands red).
                    lib.publish_check_run(dir_, pid, instance, proto, [], sha)
                    update_status_comment(
                        sf, inf, branch, pr, pid, instance, proto_path, dir_,
                        "✅ complete", max_iter, github_repository
                    )
                    lib.ensure_phase_label(dir_, pid, instance, proto, pr, "done")
                    lib.cas_push(dir_, f"{instance}: phase {_phase_id} clear → done (no further phase)")
            return

        # Remaining done case: a ROOT-LEVEL leaf whose kind is not `agent` (e.g. a
        # top-level `code` node like recover-mental-model's `combine`) — not a fork
        # leg (flat_fork_child above handles those), not a sub-pipeline leg (those
        # need a nonempty branch), not a root-child AGENT phase (handled above).
        # `branch` is always "" here (tree_path has length 1), so the retired
        # publish-hook resolution never matched a fork's branch id at this
        # position — it was a permanent no-op (always "neutral"). Nothing fed
        # this arm a verdict; record a clear outcome.
        lib.node_outcome(dir_, pid, instance, lib.state_path(proto, tree_path),
                         0, "")
        lib.publish_check_run(dir_, pid, instance, proto,
                              list(_paths.completing_scope(proto, tree_path)[1]), sha)
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

        # The unit containing this step is still RUNNING: re-publish it
        # `in_progress` (no conclusion, no fold — its steps are not all in yet).
        lib.publish_check_run(dir_, pid, instance, proto,
                              list(_paths.completing_scope(proto, tree_path)[1]),
                              sha, status="in_progress")
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

        # A FLAT nested-fork child leg (parent is a FANOUT) is its OWN terminal:
        # sf is already marked failed above; there is no leg-cursor to advance, so
        # we must NOT write the parent fork file. Only a sub-pipeline SEQUENCE
        # leg has a cursor (advance_node marks the branch file failed).
        flat_fork_child = _paths.is_fork(proto, _paths.parent_path(tree_path))
        if branch and substate and not flat_fork_child:
            ctx.cursor_sf = lib.state_file(
                dir_, pid, instance,
                path=lib.state_path(proto, _paths.parent_path(tree_path)))
            advance_node(ctx, process="failed")

        # A root-level agent-lane phase (agent OR dispatched code — see
        # _is_agent_lane_root_phase) that exhausts its iterations is a
        # terminal phase failure (label it). Before this widening, a
        # root-level dispatched `code` phase that exhausted its retries
        # would exhaust silently: no `failed` phase label, same defect class
        # as the clear-tail guard above. A fan-out leg reaching here is NOT a
        # phase terminal — join.py (fan-out) owns that.
        if _is_agent_lane_root_phase(proto, tree_path):
            lib.ensure_phase_label(dir_, pid, instance, proto, pr, "failed")

        # The step objected by exhausting its budget; publish the unit that
        # contains it (for a fork leg, the leg itself; for a root-child phase,
        # the root aggregate).
        lib.node_outcome(dir_, pid, instance, lib.state_path(proto, tree_path),
                         1, f"exhausted {max_iter} iterations")
        lib.publish_check_run(dir_, pid, instance, proto,
                              list(_paths.completing_scope(proto, tree_path)[1]), sha)
        update_status_comment(
            sf, inf, branch, pr, pid, instance, proto_path, dir_,
            f"❌ **failed** after {max_iter} iterations.",
            max_iter, github_repository
        )
        lib.cas_push(dir_, f"{instance}: iterations exhausted → failed")
        # A NESTED failed leg fires its enclosing fork's path-keyed join; the TOP
        # fork (or legacy depth-<=3) fires a path-less join (byte-identical).
        fire_join(pid, instance, branch, _join_path(proto, tree_path))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# join.py <state_workdir> <instance-key> <protocol.json>
# Fan-out barrier evaluator. Reads every branch state file for the instance; once
# ALL branches are terminal (done/failed) and the instance is not yet joined, sets
# the aggregate check-run (success iff every branch is `done`, else failure),
# renders the status comment, marks _instance.yaml joined, and CAS-pushes. Idempotent.
# Env: GITHUB_REPOSITORY, PUBLISH_TOKEN, PR, PR_HEAD_SHA, ENGINE_LOCAL.
import json
import os
import sys

# Allow importing lib from the same directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
import paths


def _nested_join(dir_, instance, proto_path, pid):
    """Evaluate a NESTED fanout barrier addressed by NODE_PATH (tree path length
    > 1, e.g. ["preflight","deep","analyze"]). On all-done it bubbles: writes the
    path-keyed __join.yaml marker, advances the ENCLOSING sub-pipeline cursor to
    the join's `.next` sub-state, and re-dispatches protocol-continue with
    client_payload[path] of that next node so the recursive walker resumes the
    sub-pipeline. On all-terminal-but-failed it bubbles a leg FAILURE up the
    enclosing fanout (mirroring the AND-barrier). Idempotent on the marker.

    The TOP-level join (NODE_PATH unset) NEVER reaches here — main() routes it to
    the legacy path, byte-identical."""
    with open(proto_path) as f:
        protocol = json.load(f)
    fanout_path = os.environ.get("NODE_PATH", "").split(".")

    marker_file_path = lib.state_path(protocol, fanout_path)
    marker = lib.read_join(dir_, pid, instance, marker_file_path)
    if marker.get("joined"):
        sys.stderr.write(f"[join] {pid}/{instance} nested {'.'.join(fanout_path)} "
                         f"already joined; no-op\n")
        return

    fanout_node = paths.node_at_path(protocol, fanout_path)
    branches = lib.resolve_leg_ids(dir_, pid, instance, fanout_path, fanout_node)

    all_terminal = True
    for b in branches:
        # A flat fanout child's terminal IS its leg file; a sub-pipeline child's
        # terminal is its branch-cursor file. state_path routes either tree path
        # to the right file name (single-phase drops the leading top id).
        sf = lib.state_file(dir_, pid, instance,
                            path=lib.state_path(protocol, fanout_path + [b]))
        st = ""
        if os.path.isfile(sf):
            try:
                st = (lib.load_yaml(sf).get("state", "") or "")
            except Exception:
                st = ""
        if st == "done":
            pass
        elif st == "failed":
            pass
        else:
            all_terminal = False

    if not all_terminal:
        sys.stderr.write(f"[join] {pid}/{instance} nested {'.'.join(fanout_path)} "
                         f"not all terminal yet; waiting\n")
        return

    # The enclosing sub-pipeline cursor (parent of this fanout, e.g. deep.yaml).
    parent_path = paths.parent_path(fanout_path)
    cursor_sf = lib.state_file(dir_, pid, instance,
                               path=lib.state_path(protocol, parent_path))

    # Count `done` legs and resolve this fanout's join state + policy up front.
    done_count = 0
    for b in branches:
        sf = lib.state_file(dir_, pid, instance,
                            path=lib.state_path(protocol, fanout_path + [b]))
        if os.path.isfile(sf):
            try:
                if (lib.load_yaml(sf).get("state") or "") == "done":
                    done_count += 1
            except Exception:
                pass

    fo_id = fanout_path[-1]
    join_state = None
    for st in protocol.get("states", []) + paths.children(protocol, parent_path):
        if st.get("kind") == "join" and st.get("of") == fo_id:
            join_state = st
            break
    policy = (join_state or {}).get("policy", "all")
    policy_ok = lib.join_policy_satisfied(policy, done_count, len(branches))

    if not policy_ok:
        # AND-barrier failure: mark the nested marker joined-with-failure, set the
        # enclosing sub-pipeline cursor failed, and fire the ENCLOSING fanout's
        # join (path-keyed if itself nested, path-less if it is the TOP fanout).
        lib.write_join(dir_, pid, instance, marker_file_path,
                       {"joined": True, "failed": True})
        cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
        cur["state"] = "failed"
        lib.dump_yaml(cursor_sf, cur)
        leg_branch = parent_path[-1] if parent_path else ""
        lib.cas_push(dir_, f"{instance}: nested join {'.'.join(fanout_path)} failed "
                           f"→ leg {leg_branch} failed")
        efp = paths.enclosing_fanout_path(protocol, parent_path)
        fields = {"protocol": pid, "instance": instance}
        if efp and len(efp) > 1:
            fields["path"] = ".".join(efp)
        lib._gh_dispatch("protocol-join", fields)
        return

    # Policy satisfied → advance to the state after the join. Prefer the join's
    # explicit `.next`; otherwise fall through to the join's next SIBLING in the
    # enclosing sub-pipeline array (e.g. a `*-rollup` declared right after the join,
    # which must still run). Only when neither exists does the leg end here.
    nxt = (join_state or {}).get("next")
    if not nxt and (join_state or {}).get("id"):
        nxt = paths.next_sibling(protocol, parent_path + [join_state["id"]])

    lib.write_join(dir_, pid, instance, marker_file_path, {"joined": True})
    cur = lib.load_yaml(cursor_sf) if os.path.isfile(cursor_sf) else {}
    if nxt:
        cur["sub_state"] = nxt
        cur["state"] = paths.enclosing_fanout_id(protocol, parent_path) \
            or cur.get("state")
        lib.dump_yaml(cursor_sf, cur)
        lib.cas_push(dir_, f"{instance}: nested join {'.'.join(fanout_path)} clear "
                           f"→ {nxt}")
        lib._gh_dispatch("protocol-continue", {
            "protocol": pid, "instance": instance,
            "path": ".".join(parent_path + [nxt]),
        })
    else:
        # No state after the join → the enclosing sub-pipeline ends here.
        cur["state"] = "done"
        lib.dump_yaml(cursor_sf, cur)
        lib.cas_push(dir_, f"{instance}: nested join {'.'.join(fanout_path)} clear "
                           f"→ leg done")
        efp = paths.enclosing_fanout_path(protocol, parent_path)
        fields = {"protocol": pid, "instance": instance}
        if efp and len(efp) > 1:
            fields["path"] = ".".join(efp)
        lib._gh_dispatch("protocol-join", fields)


def main():
    if len(sys.argv) < 4:
        sys.stderr.write("usage: join.py <state_workdir> <instance-key> <protocol.json>\n")
        sys.exit(1)

    dir_ = sys.argv[1]
    instance = sys.argv[2]
    proto = sys.argv[3]

    pid = lib.protocol_id(proto)
    pr = os.environ.get("PR", instance)  # matches join.sh PR=${PR:-$INSTANCE}; PR unset only under ENGINE_LOCAL
    sha = os.environ.get("PR_HEAD_SHA", "")

    lib.state_checkout(dir_)

    # NODE_PATH set + NESTED (tree path length > 1) → evaluate THAT fanout's
    # barrier and bubble into the enclosing sub-pipeline. NODE_PATH empty (or a
    # top fanout) falls through to the legacy _instance.yaml evaluation below,
    # which stays byte-identical.
    node_path = os.environ.get("NODE_PATH", "")
    if node_path and len(node_path.split(".")) > 1:
        _nested_join(dir_, instance, proto, pid)
        return

    inf = lib.instance_file(dir_, pid, instance)

    if not os.path.isfile(inf):
        sys.stderr.write(f"[join] no instance file for {pid}/{instance}\n")
        sys.exit(0)

    instance_data = lib.load_yaml(inf)
    if instance_data.get("joined"):   # engine only ever writes joined: true (a bool)
        sys.stderr.write(f"[join] {pid}/{instance} already joined; no-op\n")
        sys.exit(0)

    # Collect each branch's terminal state.
    with open(proto) as f:
        protocol = json.load(f)

    # Determine the fan-out phase to evaluate. Multi-phase: the cursor's phase.
    # Single-phase: the sole fan-out state (cursor absent).
    cursor_phase = instance_data.get("phase", "") or ""
    multiphase = lib.is_multiphase(protocol)
    fanout_state = None
    if multiphase and cursor_phase:
        st = lib.state_by_id(protocol, cursor_phase)
        if st and st.get("kind") == "fanout":
            fanout_state = st
    if fanout_state is None:
        for st in protocol.get("states", []):
            if st.get("kind") == "fanout":
                fanout_state = st
                break

    fo_tree_path = [fanout_state["id"]] if fanout_state else []
    branches = lib.resolve_leg_ids(dir_, pid, instance, fo_tree_path, fanout_state)
    phase_for_path = cursor_phase if (multiphase and cursor_phase) else None

    all_terminal = True
    for b in branches:
        # NOTE: a sub-pipeline branch's terminal state lives in its CURSOR file
        # (review.<b>.yaml), written by advance.py only when the LAST sub-state is
        # done. We deliberately read the cursor here, never a sub-state file.
        sf = lib.state_file(dir_, pid, instance, b, phase=phase_for_path)
        st = ""
        if os.path.isfile(sf):
            try:
                branch_data = lib.load_yaml(sf)
                st = branch_data.get("state", "") or ""
            except Exception:
                st = ""
        # Missing file → not terminal (same as join.sh: yq on missing file → "")
        if st == "done":
            pass
        elif st == "failed":
            pass
        else:
            all_terminal = False

    if not all_terminal:
        sys.stderr.write(f"[join] {pid}/{instance} not all terminal yet; waiting\n")
        sys.exit(0)

    # Count legs whose terminal state is `done` (policy operates on this).
    done_count = 0
    for b in branches:
        sf = lib.state_file(dir_, pid, instance, b, phase=phase_for_path)
        if os.path.isfile(sf):
            try:
                if (lib.load_yaml(sf).get("state") or "") == "done":
                    done_count += 1
            except Exception:
                pass

    # Resolve the join state + policy up front (needed for the policy decision).
    join_state = None
    fo_id = fanout_state.get("id") if fanout_state else None
    for st in protocol.get("states", []):
        if st.get("kind") == "join" and st.get("of") == fo_id:
            join_state = st
            break
    if join_state is None:
        for st in protocol.get("states", []):
            if st.get("kind") == "join":
                join_state = st
                break
    policy = (join_state or {}).get("policy", "all")
    policy_ok = lib.join_policy_satisfied(policy, done_count, len(branches))

    if policy_ok:
        nxt = (join_state or {}).get("next")
        # Only advance when .next names a real state in this protocol.
        # deep-fanout has `next: done` where "done" is a sentinel, not a real state —
        # guard against that by checking state_by_id returns something.
        if nxt and lib.state_by_id(protocol, nxt):
            instance_data["joined"] = True
            instance_data["phase"] = nxt
            lib.dump_yaml(inf, instance_data)
            lib.ensure_phase_label(dir_, pid, instance, protocol, pr, nxt)
            lib.cas_push(dir_, f"{instance}: join clear → continue {nxt}")
            lib.dispatch_continue(pid, instance, path=nxt)
            return

        concl = "success"
        title = "Review complete"
        summary = "All review branches completed."
    else:
        concl = "failure"
        title = "Review incomplete"
        summary = (f"Join policy '{policy}' not met "
                   f"({done_count}/{len(branches)} legs done); merge is gated.")

    lib.set_check_run(pid, sha, "completed", concl, title, summary)

    # Final shared-comment update: the closing headline now matches the aggregate.
    # Reads the comment id from _instance.yaml (inf) — the plan job created it — so
    # this only PATCHes. No-op echo under ENGINE_LOCAL.
    body = lib.render_instance_status_body(dir_, pid, instance, proto)
    lib.upsert_status_comment(inf, pr, body)

    # A fan-out phase is always terminal-before-join in the current model (its
    # `.next` is the join state), so once all branches are terminal the instance
    # is finalized here. A multi-fan-out pipeline would instead advance from the
    # JOIN state's `.next`; that is intentionally not supported yet.
    instance_data["joined"] = True
    lib.dump_yaml(inf, instance_data)
    lib.ensure_phase_label(dir_, pid, instance, protocol, pr,
                           "done" if concl == "success" else "failed")
    lib.cas_push(dir_, f"{instance}: join → {concl} (all branches terminal)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Pure tree navigation over a protocol dict + a node-path (list of ids).
No I/O, no git — addressing and structural relations only. The path is the
arbitrary-depth generalization of the fixed (phase, branch, substate) tuple."""

# The two HUMAN-TASK kinds (BPMN 2.0: User Task). Both pause the pipeline for a
# human; they differ in what the human supplies — `approval` a decision
# (/approve · /request-changes · /reject), `question` data (/answer <id>: <val>)
# for an earlier node's questions. Most engine sites only need "is this a human
# task" (is_human_task); only the resolve/answer paths and the traversal
# enumerator care which.
HUMAN_TASK_KINDS = ("approval", "question")

_LEAF_KINDS = ("agent", "code", "choice", "join") + HUMAN_TASK_KINDS

# The WHOLE vocabulary — every kind the engine implements. Kept here beside the
# other kind tuples so there is one list rather than one per module; the schema's
# `kind` enum must match it exactly, which tests/engine/test_kind_vocabulary.py
# asserts in both directions. Used by lib.validate_protocol to reject an unknown
# kind up front, which matters because the JSON-Schema layer is a DEV-ONLY
# dependency and does not run on a client's machine.
NODE_KINDS = _LEAF_KINDS + ("fork", "sequence")


def is_human_task(kind):
    """True iff `kind` is one of the human-task kinds (BPMN User Task)."""
    return kind in HUMAN_TASK_KINDS


def _root_children(proto):
    return proto.get("states", [])


def child_by_id(node_children, cid):
    for c in node_children:
        if c.get("id") == cid:
            return c
    return None


# Keep the private alias so any internal callers keep working unchanged.
_child_by_id = child_by_id


def _is_sequence_node(node):
    """True iff `node` is a sequence — BY DECLARATION, not by key presence.

    Every leg is a sequence (lib.normalize_protocol wraps a bare agent/code leg
    at load) and every sequence declares its kind, so there is nothing left to
    infer. The protocol ROOT is a sequence too but is never addressed by an id
    (the empty path), so it never reaches here.
    """
    return bool(node) and node.get("kind") == "sequence"


def node_at_path(proto, path):
    """Return the protocol node addressed by `path`, or None."""
    # Level 0 children are the protocol's top-level states (a sequence).
    cur_children = _root_children(proto)
    cur = None
    for i, seg in enumerate(path):
        if cur is None or _is_sequence_node(cur):
            # selecting a child of a sequence (root or sub-pipeline)
            container = cur_children if cur is None else cur.get("states", [])
            cur = _child_by_id(container, seg)
        elif cur.get("kind") == "fork":
            if cur.get("expand"):
                # dynamic fork: any runtime leg id maps to the `each` template
                # (a flat unit or a sub-pipeline sequence). The next loop iteration
                # then descends into `each["states"]` for a sub-pipeline each.
                cur = cur.get("each")
            else:
                cur = _child_by_id(cur.get("branches", []), seg)
        else:
            return None  # tried to descend into a leaf
        if cur is None:
            return None
    return cur


def children(proto, path):
    node = node_at_path(proto, path)
    if node is None:
        return []
    if node.get("kind") == "fork":
        return node.get("branches", [])
    if _is_sequence_node(node):
        return node.get("states", [])
    return []


def node_kind(proto, path):
    node = node_at_path(proto, path)
    if node is None:
        return ""
    if node.get("kind") == "fork":
        return "fork"
    if _is_sequence_node(node):
        return "sequence"
    k = node.get("kind", "")
    # A validated protocol's fork leg always declares `kind` (lib._validate_leg
    # rejects one that doesn't) — this fallback is for a caller that reaches
    # `node_kind` on data `validate_protocol` hasn't seen (e.g. an ad-hoc dict
    # in a test), where a kind-less node with no `states` defaults to `agent`,
    # matching the pre-declared-kind inference it once ran everywhere.
    if not k:
        return "agent"
    return k


def is_fork(proto, path):
    return node_kind(proto, path) == "fork"


def is_sequence(proto, path):
    return node_kind(proto, path) == "sequence"


def is_leaf(proto, path):
    return node_kind(proto, path) in _LEAF_KINDS


def parent_path(path):
    return list(path[:-1])


def first_child_id(node):
    if node is None:
        return None
    if node.get("kind") == "fork":
        bs = node.get("branches", [])
        return bs[0]["id"] if bs else None
    if _is_sequence_node(node):
        ss = node.get("states", [])
        return ss[0]["id"] if ss else None
    return None


def next_sibling(proto, path):
    """Id of the next child within the enclosing sequence, or None.
    Only sequences have an ordered `next`; a fork's branches are unordered."""
    if not path:
        return None
    parent = node_at_path(proto, parent_path(path)) if len(path) > 1 else None
    if parent is None:
        # enclosing scope is the protocol root (a sequence)
        siblings = _root_children(proto)
    elif _is_sequence_node(parent):
        siblings = parent.get("states", [])
    else:
        return None  # parent is a fork: branches have no ordered successor
    ids = [c["id"] for c in siblings]
    last = path[-1]
    if last in ids:
        i = ids.index(last)
        if i + 1 < len(ids):
            return ids[i + 1]
    return None


def enclosing_fork_id(proto, path):
    """Id of the nearest fork ancestor of `path` (the leg's life-state)."""
    for k in range(len(path) - 1, -1, -1):
        anc = path[:k + 1]
        if node_kind(proto, anc) == "fork":
            return anc[-1]
    return None


def enclosing_fork_path(proto, path):
    """FULL tree path of the nearest fork ancestor of `path`, or None.
    e.g. for ["preflight","deep","analyze","sec"] -> ["preflight","deep","analyze"];
    for a top leg ["preflight","quick"] -> ["preflight"]. Used to tell join.py
    which fork a completing leg belongs to (Task 12)."""
    for k in range(len(path) - 1, -1, -1):
        anc = path[:k + 1]
        if node_kind(proto, anc) == "fork":
            return list(anc)
    return None


def completing_scope(proto, path):
    """What does finishing the node at `path` complete — a GROUP, a fork LEG, or
    the whole RUN? Returns ("group"|"leg"|"root", scope_path).

    The engine repeatedly needs this and got it wrong in two different ways,
    each fixed at only one of the sites that shared the assumption:

      - `len(path) > 1` meant "inside a fork leg" — false once a top-level
        `sequence` put nodes at depth 2 with no fork above them at all.
      - `enclosing_fork_path(...) is not None` meant "IS the leg" — false for a
        group nested INSIDE a leg, which has an enclosing fork while finishing
        it ends only the group.

    The correct question is what the node's ENCLOSING SCOPE is, and whether that
    scope is itself a fork's leg or a group. Centralized here so the answer has
    exactly one definition: next.py and advance.py must not re-derive it.
    """
    scope = parent_path(path)
    if not scope:
        return "root", []
    if node_kind(proto, scope) == "fork":
        # The node is itself a FLAT leg (an agent/code branch, no sub-pipeline).
        # Finishing it ends that leg; the scope to report is the leg, i.e. the
        # node's own path.
        return "leg", list(path)
    # Otherwise the scope is a fork LEG iff its own parent is a fork. Anything
    # else that holds children — a root-level group, or a group nested inside a
    # leg — is a group, and finishing it ends the group only.
    owner = parent_path(scope)
    if owner and node_kind(proto, owner) == "fork":
        return "leg", list(scope)
    return "group", list(scope)


def path_depth(path):
    return len(path)


def _leg_paths(proto, prefix, node):
    """Yield every leaf leg path under `node` (for static depth)."""
    if node.get("kind") == "fork":
        if node.get("expand"):
            # dynamic fork: no static branches — count the `each` template as
            # one representative leg so its subtree contributes to static depth.
            each = node.get("each") or {}
            return _leg_paths(proto, prefix + ["<each>"], each)
        out = []
        for b in node.get("branches", []):
            out += _leg_paths(proto, prefix + [b["id"]], b)
        return out
    if _is_sequence_node(node):
        out = []
        for s in node.get("states", []):
            out += _leg_paths(proto, prefix + [s["id"]], s)
        return out
    return [prefix]


def max_static_depth(proto):
    depths = [0]
    for s in _root_children(proto):
        for lp in _leg_paths(proto, [s["id"]], s):
            depths.append(len(lp))
    return max(depths)


def root_ids(proto):
    return [c["id"] for c in _root_children(proto)]


def is_root_child(proto, path):
    return len(path) == 1 and path[0] in root_ids(proto)


def publishing_units(proto):
    """Every node path that publishes ONE check-run: the root sequence (the
    empty path -- the aggregate gating check-run), every `sequence` node, and
    every fork LEG whatever its kind.

    A fork leg is this engine's analogue of a GitHub Actions job: the unit that
    runs in parallel, owns a state file, and is arbitrated by a barrier. A bare
    `agent` or `code` leg is a job with exactly one step, so it publishes too.
    A `fork` itself does not (its work belongs to its legs) and neither does a
    `join` (it records into the instance file).

    A dynamic fork's legs are runtime-keyed, so the `each` template stands in
    under the sentinel id "<each>" -- mirroring _leg_paths, so the static shape
    is enumerable for linting and tests.
    """
    out = [[]]                      # the root IS a sequence

    def walk(node, node_path):
        # node_path is the node's OWN path; walk receives it pre-extended by the caller
        if node.get("kind") == "fork":
            if node.get("expand"):
                legs = [("<each>", node.get("each") or {})]
            else:
                legs = [(b["id"], b) for b in node.get("branches", []) if b.get("id")]
            for lid, leg in legs:
                lp = node_path + [lid]
                out.append(lp)      # EVERY leg publishes, sequence or not
                for s in leg.get("states", []) or []:
                    walk(s, lp + [s["id"]])
            return
        if isinstance(node.get("states"), list):
            out.append(list(node_path))
            for s in node["states"]:
                walk(s, node_path + [s["id"]])

    for st in _root_children(proto):
        walk(st, [st["id"]])
    return out

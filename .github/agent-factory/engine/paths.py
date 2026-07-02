#!/usr/bin/env python3
"""Pure tree navigation over a protocol dict + a node-path (list of ids).
No I/O, no git — addressing and structural relations only. The path is the
arbitrary-depth generalization of the fixed (phase, branch, substate) tuple."""

_LEAF_KINDS = ("agent", "gate", "merge", "join", "deterministic")


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
    # A branch with `states` is a sub-pipeline (sequence). The protocol root is a
    # sequence too but is never addressed by an id (the empty path).
    return bool(node) and isinstance(node.get("states"), list)


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
        elif cur.get("kind") == "fanout":
            if cur.get("expand"):
                # dynamic fanout: any runtime leg id maps to the `each` template
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
    if node.get("kind") == "fanout":
        return node.get("branches", [])
    if _is_sequence_node(node):
        return node.get("states", [])
    return []


def node_kind(proto, path):
    node = node_at_path(proto, path)
    if node is None:
        return ""
    if node.get("kind") == "fanout":
        return "fanout"
    if _is_sequence_node(node):
        return "sequence"
    k = node.get("kind", "")
    # A flat fanout branch (no `kind`, no `states`) is implicitly an agent unit.
    if not k:
        return "agent"
    return k


def is_fanout(proto, path):
    return node_kind(proto, path) == "fanout"


def is_sequence(proto, path):
    return node_kind(proto, path) == "sequence"


def is_leaf(proto, path):
    return node_kind(proto, path) in _LEAF_KINDS


def parent_path(path):
    return list(path[:-1])


def first_child_id(node):
    if node is None:
        return None
    if node.get("kind") == "fanout":
        bs = node.get("branches", [])
        return bs[0]["id"] if bs else None
    if _is_sequence_node(node):
        ss = node.get("states", [])
        return ss[0]["id"] if ss else None
    return None


def next_sibling(proto, path):
    """Id of the next child within the enclosing sequence, or None.
    Only sequences have an ordered `next`; a fanout's branches are unordered."""
    if not path:
        return None
    parent = node_at_path(proto, parent_path(path)) if len(path) > 1 else None
    if parent is None:
        # enclosing scope is the protocol root (a sequence)
        siblings = _root_children(proto)
    elif _is_sequence_node(parent):
        siblings = parent.get("states", [])
    else:
        return None  # parent is a fanout: branches have no ordered successor
    ids = [c["id"] for c in siblings]
    last = path[-1]
    if last in ids:
        i = ids.index(last)
        if i + 1 < len(ids):
            return ids[i + 1]
    return None


def enclosing_fanout_id(proto, path):
    """Id of the nearest fanout ancestor of `path` (the leg's life-state)."""
    for k in range(len(path) - 1, -1, -1):
        anc = path[:k + 1]
        if node_kind(proto, anc) == "fanout":
            return anc[-1]
    return None


def enclosing_fanout_path(proto, path):
    """FULL tree path of the nearest fanout ancestor of `path`, or None.
    e.g. for ["preflight","deep","analyze","sec"] -> ["preflight","deep","analyze"];
    for a top leg ["preflight","quick"] -> ["preflight"]. Used to tell join.py
    which fanout a completing leg belongs to (Task 12)."""
    for k in range(len(path) - 1, -1, -1):
        anc = path[:k + 1]
        if node_kind(proto, anc) == "fanout":
            return list(anc)
    return None


def path_depth(path):
    return len(path)


def _leg_paths(proto, prefix, node):
    """Yield every leaf leg path under `node` (for static depth)."""
    if node.get("kind") == "fanout":
        if node.get("expand"):
            # dynamic fanout: no static branches — count the `each` template as
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

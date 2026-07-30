"""protocol-paths.py — enumerate a protocol's traversals (the test surface).

Deterministic, offline, read-only. A branch point is any node whose flow can
diverge (a gate, an iterate/block check, or an on_blocked:halt node). A traversal
is an ordered [(node_id, outcome)] walk to a terminal. Decision coverage = one
all-happy baseline + one traversal per (branch point x each non-happy outcome).

Terminals are a STATIC guess (see _terminal_for): `done`/`failed`/`halt` where
the tree alone determines the outcome, and `runtime` where it does not — a
divergence inside a fan-out (the join may tolerate the leg failure) or a gate's
own never-exhausting iterate check. `runtime` traversals still get crafted and
WALKED by the testing skill; the walk is the oracle for their real terminal.

Boundary: branch points are enumerated over the STATIC tree only. A dynamic
fan-out's leg BODY (the `each` template) is NOT descended — its leg count is a
runtime value the enumerator cannot know — so branch points strictly inside a
dynamic fan-out leg are not listed here. Those interiors are still exercised
once by the happy-path walk; statically enumerating them is future work.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402  (sibling engine module: node-tree navigation)

ENGINE_DIR = Path(__file__).resolve().parent


def load(proto_path):
    with open(proto_path) as fh:
        return json.load(fh)


def _walk_nodes(states, prefix=""):
    """Yield (id_path, node) for every node depth-first in document order.

    id_path is the dot-joined chain of node ids (the NODE_PATH convention),
    single-dot separated at every nesting level (fanout legs and sub-pipelines).
    """
    for node in states:
        nid = node.get("id", "")
        idpath = f"{prefix}.{nid}" if prefix else nid
        yield idpath, node
        if node.get("kind") == "fanout":
            for br in node.get("branches", []):
                yield from _walk_nodes([br], idpath)
        elif node.get("states"):
            yield from _walk_nodes(node["states"], idpath)


def branch_points(proto):
    out = []
    for idpath, node in _walk_nodes(proto.get("states", [])):
        if node.get("kind") == "gate":
            outcomes = ["answer"] if node.get("questions_from") else ["approve", "reject", "request-changes"]
            out.append({"path": idpath, "type": "gate", "outcomes": outcomes})
        for chk in node.get("checks", []):
            of = chk.get("on_fail", "iterate")
            name = chk.get("run", chk.get("exec", "check"))
            if of == "iterate":
                out.append({"path": f"{idpath}::{name}", "type": "iterate", "outcomes": ["pass", "exhaust"]})
            # advisory and block are NOT independent branch points: advisory never
            # diverges the process axis, and a block-severity failure sets a
            # `blocking` flag (lib.decide) rather than a terminal — the real
            # divergence is only realized by an on_blocked:"halt" consumer, which
            # is captured below as the node's own `::__blocked` branch.
        if node.get("on_blocked") == "halt":
            out.append({"path": f"{idpath}::__blocked", "type": "blocked", "outcomes": ["pass", "block"]})
    return out


def happy_path(proto):
    decisions = {bp["path"]: bp["outcomes"][0] for bp in branch_points(proto)}
    return {"id": 0, "decisions": decisions, "terminal": "done"}


_FAILED = "failed"
_HALT = "halt"
_RUNTIME = "runtime"


def _node_map(proto):
    return {idpath: node for idpath, node in _walk_nodes(proto.get("states", []))}


def _branch_ctx(nodemap, bp_path):
    """(node_kind, inside_fanout) for a branch-point path (`id.path::check` or
    `id.path` or `id.path::__blocked`). `inside_fanout` is True iff any STRICT
    ancestor node is a fanout — the enclosing join may then declare a real
    `.next` that TOLERATES this leg's failure, so the pipeline terminal is not
    statically knowable."""
    idp = bp_path.split("::")[0]
    segs = idp.split(".")
    node_kind = nodemap.get(idp, {}).get("kind")
    inside_fanout = any(
        nodemap.get(".".join(segs[:i]), {}).get("kind") == "fanout"
        for i in range(1, len(segs))
    )
    return node_kind, inside_fanout


def _terminal_for(btype, outcome, inside_fanout=False, node_kind=None):
    """Best STATIC guess at the pipeline terminal a branch reaches.

    The enumerator sees only the static tree — never a join's runtime `.next`
    tolerance or a conclude hook's fail-safe. So it declines to guess a
    definite terminal in the two cases where those layers routinely override a
    naive one, reporting `runtime` ("walk it to find out") instead:
      * A divergence INSIDE a fanout — the enclosing join may absorb the failed
        leg (-> the pipeline still reaches done, or a downstream conclude hook
        blocks); a leg failure is NOT a pipeline failure (the two-axis design).
      * A GATE's own iterate check (e.g. answers-coverage) — a gate has no
        iteration counter and no max_iterations; an unsatisfiable answer parks
        it OPEN forever (do_answer records a partial answer and returns). It
        never exhausts to `failed`.
    Only a root-level (non-leg) agent-node iterate exhaust deterministically
    fails the whole pipeline; a root gate reject fails, request-changes/
    on_blocked:halt halts."""
    if inside_fanout:
        return _RUNTIME
    if btype == "gate":
        return _FAILED if outcome == "reject" else _HALT   # request-changes → halt
    if btype == "blocked":
        return _HALT
    if node_kind == "gate":                                # gate's own iterate check
        return _RUNTIME
    return _FAILED   # root agent-node iterate:exhaust


def enumerate_traversals(proto):
    base = happy_path(proto)
    out = [dict(base, branch=None)]
    nodemap = _node_map(proto)
    nid = 1
    for bp in branch_points(proto):
        node_kind, inside_fanout = _branch_ctx(nodemap, bp["path"])
        for outcome in bp["outcomes"][1:]:   # skip the happy outcome
            decisions = dict(base["decisions"])
            decisions[bp["path"]] = outcome
            out.append({
                "id": nid,
                "decisions": decisions,
                "terminal": _terminal_for(bp["type"], outcome, inside_fanout, node_kind),
                "branch": {"path": bp["path"], "outcome": outcome},
            })
            nid += 1
    return out


_TERM_SYMBOL = {"done": "✅ done", "failed": "❌ failed", "halt": "⊘ halt",
                "runtime": "◦ runtime-determined"}


def _top_level_ids(proto):
    ids = []
    for node in proto.get("states", []):
        nid = node.get("id", "")
        tag = "[fanout]" if node.get("kind") == "fanout" else ("[gate]" if node.get("kind") == "gate" else "")
        ids.append((nid, tag))
    return ids


def render(proto, traversal):
    branch = traversal.get("branch")
    # the top-level id that the diverging branch-point path belongs to
    diverge_top = branch["path"].split("::")[0].split(".")[0] if branch else None
    parts = []
    for nid, tag in _top_level_ids(proto):
        seg = f"{nid}{tag}"
        if branch and nid == diverge_top:
            seg += f"({branch['outcome']}: {branch['path']})"
        parts.append(seg)
    schematic = " → ".join(parts) + " → " + _TERM_SYMBOL[traversal["terminal"]]
    if branch and traversal["terminal"] == _RUNTIME:
        gloss = (f"At `{branch['path']}` take the `{branch['outcome']}` outcome. "
                 f"This diverges inside a fan-out (or at a gate that never "
                 f"exhausts), so the pipeline terminal is runtime-determined — "
                 f"the enclosing join may tolerate the leg failure (→ done), a "
                 f"downstream conclude hook may block, or the run may park open. "
                 f"Walk it to see which.")
    elif branch:
        gloss = (f"At `{branch['path']}` take the `{branch['outcome']}` outcome → "
                 f"pipeline reaches {traversal['terminal']}.")
    else:
        gloss = "The all-approve / all-pass happy path → done."
    return {"schematic": schematic, "gloss": gloss}


def validate_traversal(proto, proposed):
    bps = {bp["path"]: (bp["outcomes"], bp["type"]) for bp in branch_points(proto)}
    for path, outcome in proposed.get("decisions", {}).items():
        if path not in bps:
            return {"feasible": False,
                    "reason": f"no branch point at `{path}` in this protocol"}
        outcomes, btype = bps[path]
        if not (outcome in outcomes or (outcome == "recover" and btype == "iterate")):
            return {"feasible": False,
                    "reason": f"outcome `{outcome}` not available at `{path}` "
                              f"(available: {outcomes})"}
    return {"feasible": True}


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: protocol-paths.py <protocol.json>\n")
        return 2
    proto = load(sys.argv[1])
    trs = enumerate_traversals(proto)
    print(f"{len(branch_points(proto))} branch point(s); "
          f"{len(trs)} decision-coverage traversal(s).\n")
    print("== decision coverage ==")
    for t in trs:
        r = render(proto, t)
        print(f"#{t['id']}  {r['schematic']}")
        print(f"      {r['gloss']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""protocol-lint.py — validate a protocol.json and draw it as an ASCII tree.

An authoring aid for protocol authors. It runs two layers of validation and then
renders the protocol as a human-readable tree so you can eyeball the shape:

  1. STRUCTURAL — against protocol.schema.json (the strict authoring schema),
     using the `jsonschema` library *if it is importable*. jsonschema is a
     dev-only dependency; when it is absent this layer is skipped with a note and
     only the semantic layer runs. The engine itself never needs jsonschema.
  2. SEMANTIC — the engine's own authoring rules (lib.validate_protocol:
     join.of in scope, agent/flat-branch has a workflow, gate.questions_from
     names a sibling) plus the max_depth cap (lib.check_depth).

Usage:
    protocol-lint.py <path/to/protocol.json> [--no-viz]

Exit codes:  0 valid · 1 invalid · 2 usage / unreadable / unparseable input.

This file ships inside the engine directory so the `dist/` installer vendors it
into every target repo — protocol authors there get the same tool.
"""

import json
import os
import sys
from pathlib import Path

# The engine dir is this file's home; make `lib`/`paths` importable when the tool
# is run directly (python3 protocol-lint.py ...).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import lib  # noqa: E402  (engine semantic rules: validate_protocol, check_depth)
import paths  # noqa: E402  (pure tree navigation: max_static_depth)

SCHEMA_PATH = _HERE / "protocol.schema.json"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
class Report:
    """Outcome of validating one protocol dict."""

    def __init__(self):
        self.structural_errors = []  # schema-layer problems (strict; engine-ignored)
        self.semantic_errors = []    # engine-rule problems (the engine enforces these)
        self.schema_skipped = False  # True iff the structural layer was skipped

    @property
    def errors(self):
        return self.structural_errors + self.semantic_errors

    @property
    def ok(self):
        return not self.errors

    @property
    def renderable(self):
        """The tree/diagram can be drawn iff the structure is sound — i.e. the
        engine's semantic rules pass. Schema-only nits (an extra key, a wrong
        type) don't stop a best-effort render."""
        return not self.semantic_errors


def _structural_errors(proto, schema_path, jsm):
    """Validate `proto` against the JSON Schema at `schema_path` using module
    `jsm` (the imported jsonschema). Returns a list of error strings."""
    schema = json.loads(Path(schema_path).read_text())
    cls = jsm.validators.validator_for(schema)
    cls.check_schema(schema)  # the schema itself must be valid draft-07
    validator = cls(schema)
    out = []
    for e in sorted(validator.iter_errors(proto), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in e.path) or "<root>"
        out.append(f"schema: {where}: {e.message}")
    return out


def validate(proto, schema_path=SCHEMA_PATH, jsonschema_module="auto"):
    """Validate a parsed protocol dict. Returns a Report.

    `jsonschema_module`:
      "auto"  — import jsonschema if available, else skip the structural layer.
      None    — skip the structural layer (semantic-only).
      module  — use the given module for the structural layer.
    """
    report = Report()

    # Layer 1 — structural (best-effort; degrades gracefully).
    jsm = jsonschema_module
    if jsm == "auto":
        try:
            import jsonschema as jsm  # type: ignore
        except ImportError:
            jsm = None
    if jsm is None:
        report.schema_skipped = True
    else:
        try:
            report.structural_errors.extend(
                _structural_errors(proto, schema_path, jsm))
        except Exception as exc:  # noqa: BLE001 — surface, don't crash
            report.structural_errors.append(f"schema: validator error: {exc}")

    # Layer 2 — semantic (the engine's own rules + the depth cap).
    for rule in (lib.validate_protocol, lib.check_depth):
        try:
            rule(proto)
        except ValueError as exc:
            report.semantic_errors.append(str(exc))

    return report


# --------------------------------------------------------------------------- #
# ASCII tree
# --------------------------------------------------------------------------- #
def _kind(node):
    """The display kind of a node dict, mirroring paths.node_kind semantics."""
    if node.get("kind") == "fanout":
        return "fanout"
    if isinstance(node.get("states"), list):
        return "sequence"          # a sub-pipeline fan-out leg
    return node.get("kind") or "agent"  # a flat branch has no kind => agent leg


def _node_children(node):
    if node.get("kind") == "fanout":
        return node.get("branches", [])
    if isinstance(node.get("states"), list):
        return node["states"]
    return []


def _checks_line(node):
    """Group a node's checks by on_fail severity, e.g.
    'checks: a, b [iterate] · c [block] · d [advisory]'."""
    checks = node.get("checks") or []
    if not checks:
        return None
    by_sev = {}
    for c in checks:
        sev = c.get("on_fail", "iterate")
        name = c.get("run") or os.path.basename(c.get("exec", "")) or "?"
        by_sev.setdefault(sev, []).append(name)
    # iterate first (the default/common path), then block, then advisory, then any
    order = ["iterate", "block", "advisory"]
    groups = [s for s in order if s in by_sev] + [
        s for s in by_sev if s not in order
    ]
    parts = [f"{', '.join(by_sev[s])} [{s}]" for s in groups]
    return "checks: " + " · ".join(parts)


def _inputs_line(node):
    ins = node.get("inputs") or []
    if not ins:
        return None
    return "inputs: " + ", ".join(
        f"{i.get('as', '?')}←{i.get('from', '?')}" for i in ins
    )


def _arrow(node):
    nxt = node.get("next")
    return f"  → {nxt}" if nxt else ""


def _headline(node, in_fanout):
    """The single-line summary for a node (no connector/prefix)."""
    nid = node.get("id", "<unnamed>")
    kind = _kind(node)

    if kind == "fanout":
        return f"{nid}   [fanout]{_arrow(node)}"
    if kind == "sequence":
        return f"{nid}   (pipeline leg)"
    if kind == "join":
        return f"{nid}   [join]  of={node.get('of', '?')}{_arrow(node)}"
    if kind == "gate":
        if node.get("questions_from"):
            head = f"[gate·data]  questions_from={node['questions_from']}"
        else:
            head = "[gate·approval]"
            if node.get("approve_excludes_author"):
                head += "  approve_excludes_author=true"
        return f"{nid}   {head}{_arrow(node)}"
    if kind == "merge":
        return f"{nid}   [merge]  hook={node.get('hook', '?')}{_arrow(node)}"

    # agent — either a top-level state or a flat fan-out leg.
    tag = "(leg)" if in_fanout else "[agent]"
    bits = [tag]
    if node.get("workflow"):
        bits.append(f"workflow={node['workflow']}")
    if node.get("max_iterations"):
        bits.append(f"iters≤{node['max_iterations']}")
    return f"{nid}   {' '.join(bits)}{_arrow(node)}"


def _detail_lines(node):
    """Indented secondary lines for a node (checks, hooks, inputs)."""
    lines = []
    cl = _checks_line(node)
    if cl:
        lines.append(cl)
    hook_bits = []
    if node.get("conclude"):
        hook_bits.append(f"conclude={node['conclude']}")
    if node.get("on_blocked"):
        hook_bits.append(f"on_blocked={node['on_blocked']}")
    if node.get("publish"):
        hook_bits.append(f"publish={node['publish']}")
    if hook_bits:
        lines.append(" ".join(hook_bits))
    il = _inputs_line(node)
    if il:
        lines.append(il)
    return lines


def _render(node, prefix, is_last, in_fanout, out):
    connector = "└─ " if is_last else "├─ "
    out.append(prefix + connector + _headline(node, in_fanout))

    child_pad = "   " if is_last else "│  "
    detail_prefix = prefix + child_pad + "     "
    for d in _detail_lines(node):
        out.append(detail_prefix + d)

    kids = _node_children(node)
    child_in_fanout = node.get("kind") == "fanout"
    for i, kid in enumerate(kids):
        _render(kid, prefix + child_pad, i == len(kids) - 1, child_in_fanout, out)


def build_tree(proto):
    """Return the protocol rendered as a multi-line ASCII tree string."""
    name = proto.get("name", "<unnamed>")
    out = [f"{name}   (protocol)"]

    trigs = proto.get("triggers") or []
    if trigs:
        labelled = ", ".join(
            f"{t.get('comment_prefix', t.get('on', '?'))}→{t.get('command', '?')}"
            for t in trigs
        )
        out.append(f"   triggers: {labelled}")

    depth = paths.max_static_depth(proto)
    cap = lib.effective_max_depth(proto)
    out.append(f"   depth: {depth} (max_depth={cap})")
    out.append("")

    states = proto.get("states") or []
    for i, st in enumerate(states):
        _render(st, "", i == len(states) - 1, False, out)

    out.append("")
    out.append("   terminals: done, failed (implicit)")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Block diagram (a BPMN-ish flow: tasks as boxes, fan-outs as fork/join lanes)
# --------------------------------------------------------------------------- #
def _checks_brief(node):
    """Compact per-severity tally, e.g. 'checks: 3×iterate, 2×block'."""
    checks = node.get("checks") or []
    if not checks:
        return None
    counts = {}
    for ch in checks:
        sev = ch.get("on_fail", "iterate")
        counts[sev] = counts.get(sev, 0) + 1
    order = ["iterate", "block", "advisory"]
    keys = [k for k in order if k in counts] + [
        k for k in counts if k not in order
    ]
    return "checks: " + ", ".join(f"{counts[k]}×{k}" for k in keys)


def _node_body(node):
    """The lines shown inside a node's box (kind-specific, compact)."""
    kind = _kind(node)
    body = []
    if kind == "agent":
        head = "agent"
        if node.get("workflow"):
            head += " · " + node["workflow"]
        if node.get("max_iterations"):
            head += f" · iters≤{node['max_iterations']}"
        body.append(head)
        cb = _checks_brief(node)
        if cb:
            body.append(cb)
        hooks = []
        if node.get("conclude"):
            hooks.append("conclude " + node["conclude"])
        if node.get("publish"):
            hooks.append("publish " + node["publish"])
        if hooks:
            body.append(" · ".join(hooks))
        if node.get("inputs"):
            body.append("inputs ← " + ", ".join(
                i.get("from", "?") for i in node["inputs"]))
    elif kind == "gate":
        if node.get("questions_from"):
            body.append("gate · data ← " + node["questions_from"])
        else:
            h = "gate · approval"
            if node.get("approve_excludes_author"):
                h += " (author excluded)"
            body.append(h)
        cb = _checks_brief(node)
        if cb:
            body.append(cb)
    elif kind == "merge":
        body.append("merge · " + node.get("hook", "?"))
        if node.get("inputs"):
            body.append("inputs ← " + ", ".join(
                i.get("from", "?") for i in node["inputs"]))
    elif kind == "join":
        body.append("join · of=" + node.get("of", "?"))
    else:
        body.append(kind)
    return body


def _box_lines(title, body, min_w=0):
    """A bordered box with the title embedded in the top border."""
    header = f"─ {title} "
    bodies = [f" {b}" for b in (body or [])] or [" "]
    inner = max([len(header), min_w] + [len(b) for b in bodies])
    top = "┌" + header + "─" * (inner - len(header)) + "┐"
    mid = ["│" + b.ljust(inner) + "│" for b in bodies]
    bot = "└" + "─" * inner + "┘"
    return [top] + mid + [bot]


def _box(node):
    return _box_lines(node.get("id", "?"), _node_body(node))


def _bar(left, right, label, total):
    """A fork/join gateway bar, e.g. '╔═ fork ▸ review ═══════╗', `total` wide."""
    head = f"{left}═ {label} "
    total = max(total, len(head) + 1)
    return head + "═" * (total - len(head) - 1) + right


def _stack(blocks):
    """Join vertical blocks with a │ / ▼ sequence-flow connector between them."""
    out = []
    for b in blocks:
        if not b:
            continue
        if out:
            out.append("│")
            out.append("▼")
        out.extend(b)
    return out


def _render_parallel(fanout, join):
    """A fan-out as a fork/join lane: a fork bar, each leg stacked inside a left
    rail (separated by a ∥ divider), then the join bar. Legs that are
    sub-pipelines recurse; nested fan-outs nest the rail."""
    legs = fanout.get("branches", []) or []
    inner = []
    for idx, br in enumerate(legs):
        if idx > 0:
            inner.append("┄┄┄┄ ∥ ┄┄┄┄")
        if isinstance(br.get("states"), list):
            inner.append(f"▸ {br.get('id', '?')} (pipeline)")
            inner.extend(_render_flow(br["states"]))
        else:
            inner.extend(_box(br))

    fid = fanout.get("id", "?")
    rail_w = max([len(l) for l in inner] + [0]) + 2  # +2 for the "║ " prefix
    out = [_bar("╔", "╗", f"fork ▸ {fid}", rail_w)]
    out += ["║ " + l if l else "║" for l in inner]
    of = join.get("of") if join else fid
    out.append(_bar("╚", "╝", f"join ▸ {of}", rail_w))
    return out


def _render_flow(nodes):
    """Render a sequence of nodes as a vertical flow. A fan-out is paired with
    its sibling join (the join whose `of` names it) into one fork/join lane."""
    seq = list(nodes or [])
    join_of = {
        n.get("of"): n for n in seq if _kind(n) == "join" and n.get("of")
    }
    consumed = set()
    blocks = []
    for n in seq:
        if n.get("id") in consumed:
            continue
        if _kind(n) == "fanout":
            j = join_of.get(n.get("id"))
            if j:
                consumed.add(j.get("id"))
            blocks.append(_render_parallel(n, j))
        else:
            blocks.append(_box(n))
    return _stack(blocks)


def build_diagram(proto):
    """Return the protocol as a top-to-bottom BPMN-ish block diagram string."""
    name = proto.get("name", "<unnamed>")
    states = proto.get("states") or []
    terminal = (states[-1].get("next") if states else None) or "end"
    flow = _render_flow(states)
    body = _stack([["○ start"], flow, [f"◉ {terminal}"]])
    return "\n".join([f"{name}   (flow)", ""] + body)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
_USAGE = ("usage: protocol-lint.py <path/to/protocol.json> "
          "[--view tree|block|both] [--no-viz]")


def main(argv):
    args = list(argv)
    show_viz = True
    if "--no-viz" in args:
        args.remove("--no-viz")
        show_viz = False
    view = "tree"
    if "--view" in args:
        i = args.index("--view")
        if i + 1 >= len(args) or args[i + 1] not in ("tree", "block", "both"):
            sys.stderr.write(_USAGE + "\n")
            return 2
        view = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        sys.stderr.write(_USAGE + "\n")
        return 2

    path = Path(args[0])
    try:
        raw = path.read_text()
    except OSError as exc:
        sys.stderr.write(f"protocol-lint: cannot read {path}: {exc}\n")
        return 2
    try:
        proto = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"protocol-lint: {path}: invalid JSON: {exc}\n")
        return 2

    report = validate(proto)

    if report.schema_skipped:
        print("note: structural (schema) validation skipped — "
              "`jsonschema` is not installed; running semantic checks only.")

    name = proto.get("name", path.stem)

    def render():
        if not show_viz:
            return
        try:
            if view in ("tree", "both"):
                print()
                print(build_tree(proto))
            if view in ("block", "both"):
                print()
                print(build_diagram(proto))
        except Exception as exc:  # noqa: BLE001 — a render glitch is not fatal
            print(f"\n(could not draw the diagram: {exc})")

    if report.ok:
        print(f"OK: {name} is a valid protocol.")
        render()
        return 0

    print(f"INVALID: {name} has {len(report.errors)} problem(s):")
    for e in report.errors:
        print(f"  - {e}")
    # Schema-only nits don't stop a best-effort render — the engine would still
    # run this protocol (it ignores unknown keys); show the shape anyway.
    if report.renderable and show_viz:
        print("\n(the problem(s) above are schema-only; the structure is sound — "
              "best-effort diagram follows)")
        render()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

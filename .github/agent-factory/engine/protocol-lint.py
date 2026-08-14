#!/usr/bin/env python3
"""protocol-lint.py — validate a protocol.json and draw it as an ASCII tree.

An authoring aid for protocol authors. It runs two layers of validation and then
renders the protocol as a human-readable tree so you can eyeball the shape:

  1. STRUCTURAL — against protocol.schema.json (the strict authoring schema),
     using the `jsonschema` library *if it is importable*. jsonschema is a
     dev-only dependency; when it is absent this layer is skipped with a note and
     only the semantic layer runs. The engine itself never needs jsonschema.
  2. SEMANTIC — the engine's own authoring rules (lib.validate_protocol:
     join.of in scope, agent/flat-branch has a workflow, question.questions_from
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

import yaml  # PyYAML — already a runtime dependency of the engine (lib.py)

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
        self.warnings = []           # advisory: never flips `ok` / the exit code
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


def validate(proto, schema_path=SCHEMA_PATH, jsonschema_module="auto", protocol_path=None):
    """Validate a parsed protocol dict. Returns a Report.

    `jsonschema_module`:
      "auto"  — import jsonschema if available, else skip the structural layer.
      None    — skip the structural layer (semantic-only).
      module  — use the given module for the structural layer.

    `protocol_path`: the path the protocol dict was loaded from (or would be
    saved to). Used ONLY by the dispatched-`code`-node checks (Task 7) to
    locate the repo root (for `dist/manifest.json` and `.github/workflows/`).
    When omitted, those checks that need a filesystem location degrade to a
    warning rather than crashing — see `_lint_dispatched_code`.
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

    # Layer 3 — dispatched `code` node collision/contract checks (Task 7).
    # Never let a lint-layer bug crash the whole tool; surface it as a note.
    try:
        dc_errors, dc_warnings = _lint_dispatched_code(proto, protocol_path)
        report.semantic_errors.extend(dc_errors)
        report.warnings.extend(dc_warnings)
    except Exception as exc:  # noqa: BLE001 — surface, don't crash
        report.warnings.append(f"code: dispatched-code lint failed unexpectedly: {exc}")

    return report


# --------------------------------------------------------------------------- #
# Dispatched `code` node checks (Task 7 — collision + contract lint)
# --------------------------------------------------------------------------- #
def _walk_nodes(proto):
    """Yield every node dict anywhere in the protocol tree, depth-first: root
    states, fork branches (including the `each` template of a dynamic fork),
    and sub-pipeline states — reusing `_node_children`, the ONE place that
    already knows how to descend into each of those shapes for the tree
    renderer, so the two views of the tree cannot drift apart."""
    def rec(node):
        yield node
        for kid in _node_children(node):
            yield from rec(kid)

    for st in proto.get("states") or []:
        yield from rec(st)


def _repo_root(protocol_path):
    """The directory containing `.github/`, walking up from `protocol_path`.
    Mirrors the convention this repo's own tests use to find the repo root
    from a nested fixture file. Returns None if `protocol_path` is falsy or
    no such ancestor exists (e.g. a protocol dict with no path, or a
    filesystem layout that isn't a checkout of this repo at all)."""
    if not protocol_path:
        return None
    try:
        for parent in Path(protocol_path).resolve().parents:
            if (parent / ".github").is_dir():
                return parent
    except OSError:
        return None
    return None


def _workflow_is_dispatchable_step(wf_path):
    """True iff `wf_path` is SHAPED like a protocol step: it declares
    `on: workflow_dispatch:` carrying an `inputs:` block, and is not the
    engine's own orchestrator (whose inputs are protocol/ref/instance).

    Used by `_reserved_workflow_names` to tell a dispatched-code workflow
    apart from repo machinery purely from FILE CONTENT — no protocol is
    consulted, which is what keeps check 1 non-circular. Every earlier
    attempt at this distinction failed by consulting protocols: excluding
    "names the protocol under test declares" made the check inert for every
    input; excluding "names ANY protocol declares" let a protocol saved in
    place under `protocols/` exempt itself; and excluding the protocol under
    test from THAT broke the realistic case where the claiming protocol IS
    the one being linted. The property is a fact about the file, so read the
    file.

    Deliberately WEAKER than check 4's full ABI (which also demands the input
    be named `protocol_context` and `cid:[` be in the run-name). A half-wired
    step — say its input is typo'd `aw_context` — is still recognisably a
    step, so it earns check 4's actionable warning and NOT a false check-1
    error calling the author's own new file pre-existing repo machinery.
    Verified against this repo: six of the seven repo-owned workflows have no
    `workflow_dispatch` at all (dispatching them is impossible, not merely
    wrong), and the seventh is the orchestrator, excluded by name.

    Best-effort: an unreadable or malformed file conservatively returns False
    (stays reserved) rather than silently exempting it."""
    try:
        doc = yaml.safe_load(wf_path.read_text())
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(doc, dict):
        return False
    on_block = doc.get("on")
    if on_block is None:
        on_block = doc.get(True)  # YAML 1.1 unquoted `on:` key coercion
    if not isinstance(on_block, dict):
        return False
    if wf_path.stem == "agentic-orchestrator":
        return False        # the engine's entry point, not a step
    wd = on_block.get("workflow_dispatch")
    if not isinstance(wd, dict):
        return False
    return isinstance(wd.get("inputs"), dict)


def _reserved_workflow_names(repo_root):
    """The set of workflow basenames (no directory, no extension) that this
    REPO already owns and that dispatching-as-a-protocol-step would break —
    the engine's own three (agentic-engine, agentic-orchestrator,
    protocol-join) PLUS every other repo-owned workflow (the actionlint
    check `lint.yml`, `agent-factory-tests.yml`, `pr-provenance-gate.yml`,
    `mm-interactive-resume.yml`, ...).

    Derived from the FILESYSTEM (every `.github/workflows/*.yml`), not a
    hand-maintained list — self-maintaining, so a new repo CI workflow is
    automatically reserved with nothing to remember, matching this repo's
    convention that a list which can drift from reality must be derived,
    never hand-typed (see `tests/conftest.py`'s protocol glob, or
    `paths.publishing_units`). Two exclusions, both filesystem/content-
    derived (never a per-protocol name list — see below for why that would
    be circular):
      - a compiled gh-aw agent's `*.lock.yml` is not itself a dispatch
        target (the source is `<name>.md`, compiled to `<name>.lock.yml`;
        only a bare `<name>.yml` is ever `gh workflow run`-able as a
        dispatched-code step), so `.lock.yml` files are skipped outright.
      - a workflow that itself DECLARES the dispatched-code ABI (`on:
        workflow_dispatch:` with a `protocol_context` input — see
        `_workflow_declares_dispatch_contract`, which is exactly check 4's
        own contract) is presumed to be a protocol's OWN dispatched-code
        workflow, not reserved. This is content-based, not name-based, on
        purpose: excluding by "is this name declared as dispatched-code
        THIS protocol" would be circular (check 1 only ever tests names the
        protocol itself declares, so that exclusion would silently defeat
        the check for every input) — none of today's 7 reserved names
        (engine + repo CI) declare a `protocol_context` input, so this
        cleanly separates the two groups without a hand-maintained list.

    A HALF-WIRED step (its `protocol_context` input typo'd, so the content
    test above does not recognise it) is rescued by the FIRST exclusion:
    a protocol declares the name, so it is a step regardless of what the
    file looks like. The author gets check 4's warning — the one naming the
    actual defect — and not a check-1 error claiming their own file is
    pre-existing repo machinery. Only a name NO protocol claims AND that
    does not declare the ABI is reserved.

    This set is DELIBERATELY NOT read from `dist/manifest.json`'s
    `engine_workflows` — that array means something narrower and different:
    "files the dist/ installer copies into a target repo". `lint.yml` is
    this repo's OWN actionlint check, not engine machinery the installer
    should ship; conflating the two would put `lint.yml` in
    `engine_workflows` and install our CI check into every customer repo.
    The reserved-name set here is a strict SUPERSET of `engine_workflows`,
    computed independently, and `dist/manifest.json` must not be touched
    for it.

    Returns `(names, note)`: `note` is None on success. When there is no
    usable repo root (a dist/-staging-dir run, or a bare protocol dict with
    no path), returns `(None, note)` — check 1 is SKIPPED with a note rather
    than crashing or guessing."""
    if repo_root is None:
        return (None,
                "could not determine the repo root from the protocol path — "
                "skipping the reserved-workflow-name collision check (1)")
    wf_dir = repo_root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return (None,
                f"{wf_dir} not found — skipping the reserved-workflow-name "
                f"collision check (1)")
    names = set()
    for p in wf_dir.glob("*.yml"):
        if p.name.endswith(".lock.yml"):
            continue
        if _workflow_is_dispatchable_step(p):
            continue  # shaped like a protocol step, not repo machinery
        names.add(p.stem)
    return names, None


def _agent_workflow_names(proto):
    """Every `workflow` value declared by an `agent` node anywhere in the
    tree (never a dispatched `code` node's — those are legal to reuse, see
    check 2's docstring below)."""
    names = set()
    for node in _walk_nodes(proto):
        if _kind(node) == "agent" and node.get("workflow"):
            names.add(node["workflow"])
    return names


def _lint_workflow_file(name, repo_root, warnings):
    """Checks 3 and 4 for one dispatched `code` workflow `name`: file
    existence + no `.md`/`.lock.yml` sibling (3), then the workflow_dispatch
    + protocol_context + `cid:[` contract (4). Both degrade to a warning —
    never an error — when the file (or the repo root) is absent, per the
    plan: the linter also runs in a dist/ staging dir or over a protocol
    authored before its workflow exists."""
    if repo_root is None:
        warnings.append(
            f"code: workflow '{name}': could not determine the repo root "
            f"from the protocol path — skipping the file-existence/contract "
            f"checks (3, 4)")
        return
    wf_path = repo_root / ".github" / "workflows" / f"{name}.yml"
    if not wf_path.is_file():
        warnings.append(
            f"code: workflow '{name}': {wf_path} not found — skipping the "
            f"file-existence/contract checks (3, 4); fine in a dist/ staging "
            f"dir or before the workflow is authored")
        return

    # Check 3: no gh-aw agent SOURCE (.md) or COMPILED lock (.lock.yml)
    # sitting beside a dispatched code workflow — those mark the file as an
    # agent's, not a plain hand-written workflow.
    for suffix in (".md", ".lock.yml"):
        sib = repo_root / ".github" / "workflows" / f"{name}{suffix}"
        if sib.is_file():
            warnings.append(
                f"code: workflow '{name}': {sib.name} sits beside "
                f"{wf_path.name} — a dispatched `code` node's workflow "
                f"should be a PLAIN hand-written workflow, not a gh-aw agent "
                f"(an agent compiles '<name>.md' to '<name>.lock.yml'; a "
                f"dispatched `code` node runs '<name>.yml' directly)")

    # Check 4: the workflow ABI a dispatched code node needs — `on:
    # workflow_dispatch:` with a `protocol_context` input, and the literal
    # `cid:[` inside `run-name` so the engine's cid-poll can find the run
    # (lib.match_run_by_cid). Read structurally with PyYAML, except the
    # `cid:[` check, which is deliberately a raw substring test: that
    # literal must appear verbatim in the RENDERED run title (it lives
    # inside a `${{ }}` expression string), not as a parsed YAML value.
    try:
        raw = wf_path.read_text()
    except OSError as exc:
        warnings.append(f"code: workflow '{name}': could not read {wf_path}: {exc}")
        return
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        warnings.append(
            f"code: workflow '{name}': {wf_path.name} is not valid YAML "
            f"({exc}) — skipping the workflow_dispatch contract check (4)")
        return
    if not isinstance(doc, dict):
        warnings.append(
            f"code: workflow '{name}': {wf_path.name} does not parse to a "
            f"mapping — skipping the workflow_dispatch contract check (4)")
        return

    # YAML 1.1 coerces an UNQUOTED `on:` mapping key to the boolean True, not
    # the string "on" (a workflow author almost never quotes it). Check both.
    on_block = doc.get("on")
    if on_block is None:
        on_block = doc.get(True)
    wd = on_block.get("workflow_dispatch") if isinstance(on_block, dict) else None
    has_wd = isinstance(on_block, dict) and "workflow_dispatch" in on_block
    has_ctx_input = False
    if isinstance(wd, dict):
        inputs = wd.get("inputs")
        has_ctx_input = isinstance(inputs, dict) and "protocol_context" in inputs

    if not (has_wd and has_ctx_input):
        warnings.append(
            f"code: workflow '{name}': {wf_path.name} must declare `on: "
            f"workflow_dispatch:` with a `protocol_context` input — the "
            f"engine dispatches it as `gh workflow run {name}.yml -f "
            f"protocol_context=<json>`")

    if "cid:[" not in raw:
        warnings.append(
            f"code: workflow '{name}': {wf_path.name} is missing the "
            f"literal `cid:[` inside its `run-name` — without it, the "
            f"engine's dispatch poll cannot correlate the run and hard-fails "
            f"after 24 polls with \"no agent run matched cid\"")


def _lint_dispatched_code(proto, protocol_path):
    """Checks 1-4 for every dispatched `code` node in `proto` (Task 7).
    Returns `(errors, warnings)`. Checks 1-2 are answerable from the
    protocol/manifest data alone, so a violation is an ERROR (flips
    protocol-lint's exit code). Checks 3-4 read the filesystem and are
    WARNINGS only, including when they cannot run at all (no repo root, no
    workflow file) — never a crash, never an error."""
    errors = []
    warnings = []

    dispatched = [n for n in _walk_nodes(proto) if lib.is_dispatched_code(n)]
    if not dispatched:
        return errors, warnings

    repo_root = _repo_root(protocol_path)

    # Check 1 — not a workflow this repo already owns (engine + repo CI).
    # Skipped (not error, not crash) when the reserved list can't be derived
    # (see `_reserved_workflow_names`).
    reserved, note = _reserved_workflow_names(repo_root)
    if note:
        warnings.append(f"code: {note}")
    for node in dispatched if reserved is not None else ():
        name = node.get("workflow")
        if name in reserved:
            errors.append(
                f"code: node '{node.get('id', '?')}' declares workflow "
                f"'{name}', which is a workflow this REPO already owns "
                f"({', '.join(sorted(reserved))}) — dispatching it would "
                f"run that machinery (`gh workflow run {name}.yml`) instead "
                f"of a protocol step")

    # Check 2 — not also an agent node's workflow in the SAME protocol. Two
    # dispatched `code` nodes sharing a workflow name is fine (the same step
    # reused); only the agent/code cross is flagged.
    agent_names = _agent_workflow_names(proto)
    for node in dispatched:
        name = node.get("workflow")
        if name in agent_names:
            errors.append(
                f"code: node '{node.get('id', '?')}' declares workflow "
                f"'{name}', which is ALSO an `agent` node's workflow in this "
                f"protocol — the two lanes dispatch different files for the "
                f"same basename (an agent's compiled '{name}.lock.yml' vs. "
                f"a dispatched code node's plain '{name}.yml'); pick "
                f"distinct names")

    # Checks 3-4 — filesystem, once per unique name (reuse across nodes is
    # legal and should not multiply warnings).
    seen = set()
    for node in dispatched:
        name = node.get("workflow")
        if name in seen:
            continue
        seen.add(name)
        _lint_workflow_file(name, repo_root, warnings)

    return errors, warnings


# --------------------------------------------------------------------------- #
# ASCII tree
# --------------------------------------------------------------------------- #
def _kind(node):
    """The display kind of a node dict, mirroring paths.node_kind semantics."""
    if node.get("kind") == "fork":
        return "fork"
    if isinstance(node.get("states"), list):
        return "sequence"          # a sub-pipeline fan-out leg
    return node.get("kind") or "agent"  # a flat branch has no kind => agent leg


def _node_children(node):
    if node.get("kind") == "fork":
        # Dynamic fork: no static branches[] — show the `each` template as the
        # single (runtime-replicated) leg shape so the tree isn't empty.
        if node.get("expand"):
            each = dict(node.get("each", {}))
            each.setdefault("id", f"«each ×{node['expand'].get('id_from','?')}»")
            return [each]
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


def _headline(node, in_fork):
    """The single-line summary for a node (no connector/prefix)."""
    nid = node.get("id", "<unnamed>")
    kind = _kind(node)

    if kind == "fork":
        return f"{nid}   [fork]{_arrow(node)}"
    if kind == "sequence":
        return f"{nid}   (pipeline leg)"
    if kind == "join":
        return f"{nid}   [join]  of={node.get('of', '?')}{_arrow(node)}"
    if paths.is_human_task(kind):
        if kind == "question":
            head = f"[question]  questions_from={node.get('questions_from', '?')}"
        else:
            head = "[approval]"
            if node.get("approve_excludes_author"):
                head += "  approve_excludes_author=true"
        return f"{nid}   {head}{_arrow(node)}"
    if kind == "choice":
        on = node.get("on") or {}
        arms = " · ".join(f"{c.get('when')!r}→{c.get('next')}"
                          for c in (node.get("cases") or []))
        if node.get("default"):
            arms += f" · else→{node['default']}"
        return f"{nid}   [choice]  on {on.get('from', '?')}{on.get('path', '')}  {arms}"
    if kind == "code":
        return f"{nid}   [code]  script={node.get('script', '?')}{_arrow(node)}"

    # agent — either a top-level state or a flat fan-out leg.
    tag = "(leg)" if in_fork else "[agent]"
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
    if hook_bits:
        lines.append(" ".join(hook_bits))
    il = _inputs_line(node)
    if il:
        lines.append(il)
    return lines


def _render(node, prefix, is_last, in_fork, out):
    connector = "└─ " if is_last else "├─ "
    out.append(prefix + connector + _headline(node, in_fork))

    child_pad = "   " if is_last else "│  "
    detail_prefix = prefix + child_pad + "     "
    for d in _detail_lines(node):
        out.append(detail_prefix + d)

    kids = _node_children(node)
    child_in_fork = node.get("kind") == "fork"
    for i, kid in enumerate(kids):
        _render(kid, prefix + child_pad, i == len(kids) - 1, child_in_fork, out)


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
        if hooks:
            body.append(" · ".join(hooks))
        if node.get("inputs"):
            body.append("inputs ← " + ", ".join(
                i.get("from", "?") for i in node["inputs"]))
    elif paths.is_human_task(kind):
        if kind == "question":
            body.append("question ← " + node.get("questions_from", "?"))
        else:
            h = "approval"
            if node.get("approve_excludes_author"):
                h += " (author excluded)"
            body.append(h)
        cb = _checks_brief(node)
        if cb:
            body.append(cb)
    elif kind == "choice":
        on = node.get("on") or {}
        body.append(f"choice · {on.get('from', '?')}{on.get('path', '')}")
        for c in (node.get("cases") or []):
            body.append(f"  {c.get('when')!r} → {c.get('next')}")
        if node.get("default"):
            body.append(f"  else → {node['default']}")
    elif kind == "code":
        body.append("code · " + node.get("script", "?"))
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


def _render_parallel(fork, join):
    """A fan-out as a fork/join lane: a fork bar, each leg stacked inside a left
    rail (separated by a ∥ divider), then the join bar. Legs that are
    sub-pipelines recurse; nested fan-outs nest the rail."""
    legs = fork.get("branches", []) or (
        [dict(fork.get("each", {}), id=f"«each»")] if fork.get("expand") else [])
    inner = []
    for idx, br in enumerate(legs):
        if idx > 0:
            inner.append("┄┄┄┄ ∥ ┄┄┄┄")
        if isinstance(br.get("states"), list):
            inner.append(f"▸ {br.get('id', '?')} (pipeline)")
            inner.extend(_render_flow(br["states"]))
        else:
            inner.extend(_box(br))

    fid = fork.get("id", "?")
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
        if _kind(n) == "sequence":
            # An Embedded Subprocess: draw the group as a labelled band around
            # its children. An opaque box would hide the entire group from the
            # one view meant to show the flow.
            inner = _render_flow(n.get("states", []))
            width = max([len(x) for x in inner] or [0])
            label = f"╭─ {n.get('id', '?')} (sequence) "
            band = [label + "─" * max(0, width + 2 - len(label)) + "╮"]
            band += ["│ " + x.ljust(width) + " │" for x in inner]
            band.append("╰" + "─" * (width + 2) + "╯")
            blocks.append(band)
            continue
        if _kind(n) == "fork":
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

    report = validate(proto, protocol_path=path)

    if report.schema_skipped:
        print("note: structural (schema) validation skipped — "
              "`jsonschema` is not installed; running semantic checks only.")

    for w in report.warnings:
        print(f"warning: {w}")

    name = proto.get("name", path.stem)

    def render():
        if not show_viz:
            return
        try:
            # Validation (above) runs on the AUTHOR's tree, so its messages name
            # what they wrote. The VISUALIZATION runs on the NORMALIZED tree
            # instead — lib.normalize_protocol wraps a bare agent/code fork leg
            # into a one-child sequence (the shape the engine actually runs),
            # so the picture matches what next.py/advance.py will do, not just
            # what was typed.
            normalized = lib.normalize_protocol(proto)
            if view in ("tree", "both"):
                print()
                print(f"(a leg's `{lib.LEG_WORK_NODE_ID}` child is engine-inserted "
                      f"— lib.normalize_protocol wraps a bare agent/code fork leg "
                      f"into a one-child sequence; this diagram shows the "
                      f"normalized tree the engine runs, not the literal file)")
                print(build_tree(normalized))
            if view in ("block", "both"):
                print()
                print(build_diagram(normalized))
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

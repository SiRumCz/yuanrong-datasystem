#!/usr/bin/env python3
"""Engine shared library. Importable by the engine scripts AND a thin CLI
(`python3 lib.py <subcommand> ...`) for helpers the orchestrator calls inline."""
import copy
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import yaml
import paths as _paths

STATE_REMOTE = os.environ.get("STATE_REMOTE", "")
STATE_BRANCH = os.environ.get("STATE_BRANCH", "agentic-state")
GIT_ID = ["-c", "user.email=engine@agentic-protocol-poc",
          "-c", "user.name=protocol-engine"]

# Wall-clock bound on every author-supplied hook/check subprocess (code hooks,
# expanders, checks, publish + conclude hooks). Without it a hook that hangs —
# a curl with no timeout, a wedged toolchain, a stray input() — blocks the job
# until GitHub's 6-hour ceiling while holding the state PAT. Generous by
# default: a vendored toolchain over a large diff is legitimately slow, and
# killing honest work is worse than a late failure.
HOOK_TIMEOUT_SECONDS = 600

# The baseline environment any author-supplied subprocess (expander, code hook)
# is given. Built as a strict ALLOWLIST rather than a denylist so a future
# plan-job env var — the next PUBLISH_TOKEN — cannot leak by default; a node
# that genuinely needs a secret declares it in the node's `env` list.
HOOK_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "PR", "ENGINE_LOCAL",
                      "GITHUB_REPOSITORY", "TMPDIR")


def hook_base_env(instance):
    """The least-privilege base env for an author-supplied hook subprocess."""
    env = {k: os.environ[k] for k in HOOK_ENV_ALLOWLIST if k in os.environ}
    env.setdefault("PR", instance[len("pr-"):] if instance.startswith("pr-") else instance)
    return env


def hook_timeout_seconds():
    """The hook subprocess timeout, overridable via AGENT_FACTORY_HOOK_TIMEOUT.

    A malformed or non-positive override falls back to the default rather than
    disabling the bound — `timeout=None` means "wait forever", which is exactly
    the failure mode this exists to prevent, and a typo'd env var must not
    silently reintroduce it."""
    raw = os.environ.get("AGENT_FACTORY_HOOK_TIMEOUT", "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return HOOK_TIMEOUT_SECONDS
    return val if val > 0 else HOOK_TIMEOUT_SECONDS


_TOKEN_PATTERNS = [
    # GitHub token families: prefix + token body chars.
    re.compile(r"gh[posu]_[A-Za-z0-9_]+"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    # OpenAI / Anthropic API keys — structural, so protection does NOT depend on
    # the secret being present in THIS job's environment (per the trust-zone
    # table LLM creds live in the agent job, not advance/checks).
    re.compile(r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}"),
    # x-access-token:<secret>@ inside clone/remote URLs.
    re.compile(r"x-access-token:[^@\s/]+@"),
]
_SECRET_ENV_VARS = (
    "PUBLISH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "POC_DISPATCH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY",
)


def _redact(text):
    """Redact secrets from text before it reaches a PUBLIC check-run/comment or
    the job log. Generic (no protocol coupling): redacts the values of known
    secret env vars, GitHub token patterns, and the x-access-token:<secret>@
    form in remote URLs. Replaces each with ***.

    Lives in lib.py (not advance.py) because 4.0.0 routes EVERY check-run
    through publish_check_run, whose summary is a hook's own stdout: both hook
    kinds now reach a public surface, so both must be scrubbed by the same
    function."""
    if not text:
        return text
    out = text
    # 1) Concrete secret values from the environment (longest first so a token
    #    that is a substring of another does not leave a tail behind).
    values = sorted(
        (v for name in _SECRET_ENV_VARS for v in (os.environ.get(name, ""),) if v),
        key=len, reverse=True,
    )
    for v in values:
        out = out.replace(v, "***")
    # 2) Structural patterns (tokens that were not in our env, e.g. from stderr).
    for pat in _TOKEN_PATTERNS:
        if pat.pattern.startswith("x-access-token"):
            out = pat.sub("x-access-token:***@", out)
        else:
            out = pat.sub("***", out)
    return out


# The exit code the engine SYNTHESIZES when a hook produced no verdict at all
# (timed out, was unresolvable, or was not executable). Distinct from any exit
# code a hook itself returns, because it means something different: a nonzero
# exit is a VERDICT ("I ran and I object") and honours `on_blocked`, while this
# means "I never ran" and halts unconditionally -- the engine cannot read the
# absence of `on_blocked` as consent to continue on data that was never
# computed.
HOOK_FAILED_EXIT = 250


def run_conclude_hook(proto_path, proto, evid, instance, dir_=None, tree_path=None):
    """Run an agent node's `conclude` hook. Returns (exit_code, summary), or None
    when the node declares none.

    ABI (4.0.0): `<hook> <evidence.json> <instance>`; the EXIT CODE is the
    contract, stdout (if any) is the check-run summary. `blocked` is gone from
    the ABI -- halting is declared on the node as `on_blocked: "halt"`, where a
    reader sees it without opening a script.

    Resolution is path-aware (via `tree_path`/`_paths.node_at_path`), so it
    works at EVERY agent position -- including a flat fork leg, which ran no
    conclude hook at all before 4.0.0.

    Preserves the inputs-materialization behaviour: a node with declared
    `inputs` gets them resolved and written under a temp dir, exposed to the
    hook subprocess as `CONCLUDE_INPUTS_DIR` (e.g. code-review's
    conclude-preflight reads deeply-nested leg evidence through it).
    `CONCLUDE_STATE_DIR` is always set (to `dir_` or "") so a hook can read any
    node's persisted evidence directly.
    """
    node = _paths.node_at_path(proto, tree_path) if tree_path is not None else None
    action = (node or {}).get("conclude") or None
    if not action:
        return None
    pdir = os.path.dirname(os.path.abspath(proto_path))
    kind, path = resolve_executable(f"{pdir}/scripts", action, pdir, "").split("\t", 1)
    if kind == "ERR" or not os.access(path, os.X_OK):
        sys.stderr.write(f"[conclude] hook unresolved/not-exec: {path}\n")
        return HOOK_FAILED_EXIT, f"conclude hook `{action}` unresolved"
    # NOT hook_base_env's allowlist: conclude runs trusted in zone 4 (unlike a
    # `code`/`expand` hook), so it inherits the FULL parent env, exactly as the
    # pre-4.0.0 advance.run_conclude_hook did -- e.g. PUBLISH_TOKEN/GH_TOKEN. A
    # fenced env here would silently un-credential a live hook (a runtime 403,
    # not a local error); see docs/superpowers/specs/2026-07-28-check-run-
    # publication-design.md Non-goals. `node.get("env")` is redundant on top of
    # a full env but harmless, so it stays for parity with `code`/`expand`.
    env = dict(os.environ)
    env["CONCLUDE_STATE_DIR"] = dir_ or ""
    for name in (node.get("env") or []):
        if name in os.environ:
            env[name] = os.environ[name]
    workdir = None
    declared = list(node.get("inputs") or [])
    if dir_ is not None and declared:
        fo = _fork_state(proto)
        phase = fo["id"] if (fo and is_multiphase(proto)) else None
        consuming_branch = tree_path[-2] if tree_path and len(tree_path) >= 2 else None
        resolved = resolve_inputs(
            proto,
            dir_,
            protocol_id(proto_path),
            instance,
            consuming_branch=consuming_branch,
            consuming_phase=phase,
            inputs=declared,
            consuming_path=tree_path,
        )
        workdir = tempfile.mkdtemp(prefix="conclude-inputs-")
        materialize_inputs(resolved, workdir)
        env["CONCLUDE_INPUTS_DIR"] = os.path.join(workdir, "inputs")
    try:
        try:
            r = subprocess.run([path, evid, instance], text=True, capture_output=True,
                               env=env, timeout=hook_timeout_seconds())
        except subprocess.TimeoutExpired:
            secs = hook_timeout_seconds()
            sys.stderr.write(f"[conclude] hook timed out after {secs}s: {path}\n")
            return HOOK_FAILED_EXIT, f"conclude hook `{action}` timed out after {secs}s"
        return r.returncode, _redact((r.stdout or "").strip())
    finally:
        # Materialized inputs are only needed for the hook subprocess above;
        # remove the temp dir so repeated conclude calls don't leak it per run.
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def block_exit(blocking, conclude_result):
    """Fold the engine's `blocking` with a conclude hook's verdict into one
    (exit, summary) for a node.

    Two independent sources say "this node objects":
      - `blocking`        -- ENGINE-computed: a block-severity check failed
                             (lib.decide folds it out of the check verdicts).
      - conclude_result   -- PROTOCOL-authored: the hook's own exit code, or
                             None when the node declares no `conclude`.

    The node's outcome is the worst of the two. The hook's message wins when it
    has one: a hook that read the evidence has more to say than the generic
    floor message.

    Deliberately does NOT consult the node's halt declaration. This decides
    COLOUR only; whether objecting also STOPS the run is the node's own
    declaration, read separately by advance.py. Keeping them apart is what lets
    code-review's review legs go red and still flow into the fixer.
    """
    h_exit, h_sum = conclude_result if conclude_result is not None else (0, "")
    if blocking and not h_exit:
        return 1, "a required check did not pass"
    return h_exit, h_sum


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def dump_yaml(path, data):
    # sort_keys=False + block style keeps a stable, human-readable git trail.
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def git(dir_, *args, check=True, capture=False):
    return subprocess.run(["git", "-C", dir_] + list(args),
                          check=check, text=True, capture_output=capture)


def protocol_id(proto_path):
    """protocol_id <protocol.json> — the protocol's id."""
    with open(proto_path) as f:
        return json.load(f)["name"]


def _coord_to_path(branch=None, phase=None, substate=None):
    """Back-compat: collapse the legacy 3 kwargs to a node-path list."""
    p = []
    if phase:
        p.append(phase)
    if branch:
        p.append(branch)
    if substate:
        p.append(substate)
    return p


def state_file(d, pid, instance, branch=None, phase=None, substate=None, path=None):
    """<dir>/<pid>/<instance>/<dot-joined-path>.yaml (or <instance>.yaml for the
    empty path). `path` is the canonical node-path; the branch/phase/substate
    kwargs are a back-compat shim that builds the equivalent 3-element path.
    Depth-<=3 paths are byte-identical to the historical layout."""
    base = f"{d}/{pid}/{instance}"
    p = list(path) if path is not None else _coord_to_path(branch, phase, substate)
    if not p:
        return f"{base}.yaml"
    return f"{base}/{'.'.join(p)}.yaml"


def state_path(proto, tree_path):
    """Tree-navigation path -> file-naming path. Drop the leading top-level
    fork/phase id when single-phase (it is omitted from historical filenames),
    EXCEPT for a root `sequence`, which would otherwise collapse onto the
    instance file (see below);
    keep the full path when multi-phase. The recursive walker passes its tree
    path through this before every state_file/output_artifact_path/join_marker_file
    call, so depth-<=3 files stay byte-identical to the legacy layout."""
    if not tree_path:
        return []
    if is_multiphase(proto):
        return list(tree_path)
    dropped = list(tree_path[1:])
    if not dropped:
        # The leading-element drop exists purely for byte-identity with the
        # legacy single-phase layout, where the SOLE root node's own files were
        # unqualified. Two conditions make dropping wrong, and both are about
        # the empty path being a real file: `[]` IS `<pid>/<instance>.yaml`.
        #
        # (a) A `sequence` would write its cursor over the INSTANCE state.
        #     Scoped to `sequence` only: a root FORK also maps to the empty path,
        #     but that is the historical layout every shipped protocol already
        #     uses on the state branch (recover-mental-model, deep-review-stub,
        #     code-review-ocr, ...), and widening it would relocate their files
        #     and orphan in-flight instances.
        #
        # (b) Two root nodes would COLLIDE on it. The legacy layout was safe
        #     because there was only ever one root node owning files; a root
        #     `choice` broke that — `choice-min`'s agent, choice, and two code
        #     nodes ALL mapped to `[]`, so the choice's decision record
        #     overwrote the agent's state file and a code rollup overwrote
        #     `<instance>.evidence.json`, the very evidence the choice reads to
        #     route. Qualify them all rather than let a write land on a
        #     neighbour. Every shipped single-phase protocol has exactly one
        #     file-owning root (the fork and its join own none at their own
        #     path), so this leaves the live layout untouched — the state-path
        #     golden is what proves that.
        if _paths.node_kind(proto, tree_path) == "sequence":
            return list(tree_path)
        if len(_file_owning_roots(proto)) > 1:
            return list(tree_path)
    return dropped


_FILE_OWNING_KINDS = ("agent", "code", "choice", "sequence") + _paths.HUMAN_TASK_KINDS


def _file_owning_roots(protocol):
    """Top-level nodes that write a state/evidence file AT THEIR OWN path.

    A `fork` and its `join` are excluded: a fork's files belong to its legs (one
    level down) and a join records into the instance file, so neither occupies
    its own path. Used only to decide whether the single-phase leading-element
    drop would make two roots collide."""
    return [s for s in protocol.get("states", [])
            if s.get("kind") in _FILE_OWNING_KINDS]


def output_artifact_path(d, pid, instance, branch=None, phase=None, substate=None,
                         kind="evidence", path=None):
    """Persisted-output path for a state, parallel to state_file but with a
    .<kind>.json suffix. kind is 'evidence' (agent) or 'answers' (a human task)."""
    sf = state_file(d, pid, instance, branch=branch, phase=phase, substate=substate, path=path)
    return sf[:-len(".yaml")] + f".{kind}.json"


def join_marker_file(d, pid, instance, fork_path):
    """Path to the path-keyed join marker for a nested fork.
    `fork_path` is the FILE-NAMING path (already converted via state_path);
    callers in Task 12 pass lib.state_path(proto, tree_path).
    Only nested forks (len(tree_path) > 1) should call this — top-level
    fork join tracking stays on _instance.yaml (back-compat)."""
    base = f"{d}/{pid}/{instance}"
    return f"{base}/{'.'.join(fork_path)}.__join.yaml"


def read_join(d, pid, instance, fork_path):
    """Read the path-keyed join marker dict, or {} if it does not exist yet."""
    f = join_marker_file(d, pid, instance, fork_path)
    return load_yaml(f) if os.path.isfile(f) else {}


def write_join(d, pid, instance, fork_path, data):
    """Write (overwrite) the path-keyed join marker dict."""
    f = join_marker_file(d, pid, instance, fork_path)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    dump_yaml(f, data)


def manifest_file(d, pid, instance, tree_path):
    """Path to a dynamic fork's manifest. Unlike leg/join files this is a NEW
    file with no legacy byte-identity constraint, so it keys by the FULL tree
    path (never dropped by state_path) — always unique and non-empty, for the
    top fork (['review'] -> review.__manifest.yaml) and nested alike."""
    base = f"{d}/{pid}/{instance}"
    return f"{base}/{'.'.join(tree_path)}.__manifest.yaml"


def read_manifest(d, pid, instance, tree_path):
    """Read the manifest dict, or {} if it does not exist yet."""
    f = manifest_file(d, pid, instance, tree_path)
    return load_yaml(f) if os.path.isfile(f) else {}


def write_manifest(d, pid, instance, tree_path, data):
    f = manifest_file(d, pid, instance, tree_path)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    dump_yaml(f, data)


def resolve_leg_ids(dir_, pid, instance, tree_path, fork_node):
    """The leg-id list for a fork: the persisted manifest's ids when dynamic
    (expand present), else the static branches[] ids. The single seam that lets
    join.py treat dynamic and static forks uniformly."""
    if fork_node and fork_node.get("expand"):
        man = read_manifest(dir_, pid, instance, tree_path)
        return [leg["id"] for leg in man.get("legs", [])]
    return [b["id"] for b in (fork_node.get("branches", []) if fork_node else [])]


def fork_is_materialized(dir_, pid, instance, tree_path, fork_node):
    """True iff a `from_fork` reducer (inline `run_code_hook` or a dispatched
    `code` node) may collect this fork's legs. A DYNAMIC fork (`expand`) needs
    its manifest written — next.py's fork-entry writes one at fan-out time, so
    its absence means the fork was never entered (or `from_fork` misnamed it).
    A STATIC fork (`branches[]`) writes no manifest (only `expand` does; see
    `resolve_leg_ids`), and needs none — its legs are fixed by the protocol
    itself, so it is materialized as soon as it exists at all. The single seam
    both from_fork call sites use for the 'fork not materialized' guard, so
    the check-fork-then-collect-legs pair never drifts (mirrors the
    `resolve_leg_ids` dynamic/static split collect_fork_evidence also uses)."""
    if (fork_node or {}).get("expand"):
        return os.path.isfile(manifest_file(dir_, pid, instance, tree_path))
    return bool((fork_node or {}).get("branches"))


def _leg_terminal_substate(fork_node, lid):
    """Which sub-state within leg `lid` holds its OUTPUT evidence, or None if
    the leg is not (or not yet) a wrapped sequence. Post-6.0.0 EVERY leg —
    dynamic (`each`) or static (`branches[]`, matched by id) — is normalized
    into a one-child sequence, so this is that sequence's last `states[]` id:
    'step' for a wrapped bare agent/code leg, or an authored sub-pipeline's
    real terminal id. `fork_node` must already be the NORMALIZED node (the one
    load_protocol/normalize_protocol produced) for `states` to be present."""
    fork_node = fork_node or {}
    if fork_node.get("expand"):
        leg_cfg = fork_node.get("each") or {}
    else:
        leg_cfg = next((b for b in fork_node.get("branches", []) if b.get("id") == lid), None) or {}
    states = leg_cfg.get("states") if isinstance(leg_cfg, dict) else None
    return states[-1]["id"] if states else None


def collect_fork_evidence(dir_, pid, instance, tree_path, fork_node, proto=None):
    """Assemble the reduce input for a `merge` with from_fork: one row per leg
    in the manifest, carrying its terminal state + persisted evidence (or None).
    Reads from the state branch, never job outputs — resilient to matrix clobber.

    `tree_path` is the fork's TREE path (e.g. ['review'] for the top fork, or
    ['review', '<fileleg>', 'findings'] for a nested findings fork). When `proto`
    is given, each leg is resolved by its FULL tree path (tree_path + [lid]) via
    state_path — nested-aware. Since engine 6.0.0 EVERY leg (a dynamic `each` or
    a static `branches[]` entry) is normalized at load into a one-child sequence
    (lib.normalize_protocol / lib._wrap_leg), so a leg's real OUTPUT evidence
    ALWAYS lives one level deeper than its own cursor file, at its terminal
    sub-state (tree_path + [lid, <leg's last states[] id>]) — 'step' for a
    wrapped bare agent/code leg, or an authored sub-pipeline's real terminal id.
    The leg cursor file at tree_path + [lid] is just the sequence cursor and
    carries no evidence. This is resolved PER LEG (`_leg_terminal_substate`),
    not once for the whole fork, because a static `branches[]` fork's legs are
    equally wrapped and were silently missed when this only consulted `each`
    (evidence always read as None for a from_fork over a static fork — caught
    by the dispatched-code-stub live protocol's `scan` fork, which is static).
    When `proto` is None (back-compat), legs are resolved FLAT (branch=leg-id,
    no path prefix), matching the historical single-phase file layout used
    before nested from_fork support.

    Leg enumeration mirrors `resolve_leg_ids`: a DYNAMIC fork's legs come from
    its persisted manifest (id + the expander-derived `key`); a STATIC fork
    has no manifest (next.py only ever writes one for `expand`), so its legs
    are its `branches[]` ids directly, with `key` None (nothing was extracted
    — the branch id IS the identity)."""
    if (fork_node or {}).get("expand"):
        man = read_manifest(dir_, pid, instance, tree_path)
        legs = man.get("legs", [])
    else:
        legs = [{"id": b["id"], "key": None}
                for b in (fork_node or {}).get("branches", [])]
    rows = []
    for leg in legs:
        lid = leg["id"]
        if proto is not None:
            out_sub = _leg_terminal_substate(fork_node, lid)
            leg_fp = state_path(proto, list(tree_path) + [lid])
            sf = state_file(dir_, pid, instance, path=leg_fp)          # leg SEQUENCE CURSOR
            evid_tree = list(tree_path) + [lid] + ([out_sub] if out_sub else [])
            evid_fp = state_path(proto, evid_tree)
            evid_path = output_artifact_path(dir_, pid, instance, path=evid_fp)
        else:
            sf = state_file(dir_, pid, instance, lid)          # single-phase leg file
            evid_path = output_artifact_path(dir_, pid, instance, branch=lid, kind="evidence")
        state = ""
        if os.path.isfile(sf):
            try:
                state = load_yaml(sf).get("state", "") or ""
            except Exception:
                state = ""
        evidence = None
        if os.path.isfile(evid_path):
            try:
                with open(evid_path) as f:
                    evidence = json.load(f)
            except (json.JSONDecodeError, ValueError):
                evidence = None
        rows.append({"leg_id": lid, "key": leg.get("key"), "state": state, "evidence": evidence})
    return rows


def leg_id(raw_key):
    """Stable, filesystem-safe leg id from an item's raw id_from value.
    A short sha1 hex is alnum by construction (no sanitizing needed)."""
    return hashlib.sha1(str(raw_key).encode("utf-8")).hexdigest()[:8]


def extract_key(item, id_from):
    """Resolve a simple JSONPath (`$.a.b`) against an item dict. Only the
    dotted-`$.`-rooted form is supported (YAGNI — no wildcards/filters)."""
    if not id_from.startswith("$."):
        raise ValueError(f"id_from must start with '$.', got {id_from!r}")
    cur = item
    for seg in id_from[2:].split("."):
        if not isinstance(cur, dict) or seg not in cur:
            raise ValueError(f"id_from {id_from!r} did not resolve on item {item!r}")
        cur = cur[seg]
    return cur


def build_manifest(items, id_from, max_legs):
    """Turn the expander's items list into a manifest dict. Fails loud on
    over-cap (> max_legs) and on duplicate leg keys."""
    if len(items) > max_legs:
        raise ValueError(f"expander emitted {len(items)} items > max_legs {max_legs}")
    legs, seen = [], {}
    for item in items:
        key = extract_key(item, id_from)
        lid = leg_id(key)
        if lid in seen:
            raise ValueError(f"two items map to leg id '{lid}' (keys {seen[lid]!r} and {key!r})")
        seen[lid] = key
        legs.append({"id": lid, "key": key, "item": item})
    return {"count": len(legs), "legs": legs}


def run_expander(dir_, pid, instance, proto_path, fork_node):
    """Run a dynamic fork's trusted expander hook and return its items list.
    Resolved from <protocol-dir>/expand/<hook>. Raises ValueError (fail loud) on
    unresolved / non-executable / nonzero / non-JSON / missing-`items` output.

    Runs in zone 1 (plan); the hook re-fetches the diff itself and is handed only
    a read token via a strict env allowlist (ENFORCED, not aspirational — the
    plan job's full env, including STATE_REMOTE / PUBLISH_TOKEN / the broad
    dispatch PAT, is never forwarded). Under ENGINE_LOCAL the stub reads a
    fixture file instead."""
    pdir = os.path.dirname(os.path.abspath(proto_path))
    expand = fork_node.get("expand", {})
    res = resolve_executable(f"{pdir}/expand", expand.get("hook", ""), pdir, expand.get("exec", ""))
    kind, path = res.split("\t", 1)
    if kind == "ERR" or not os.access(path, os.X_OK):
        raise ValueError(f"expander '{expand.get('hook')}' unresolved/not-exec: {path}")
    # SECURITY (spec §5): scope the expander to a read-only token. Build the env
    # from a strict ALLOWLIST — never the plan job's full env — so STATE_REMOTE /
    # PUBLISH_TOKEN / the broad dispatch PAT are dropped by default (a future added
    # plan-job env var cannot leak). The expander gets only a read token to fetch
    # the diff.
    env = hook_base_env(instance)
    tok = os.environ.get("EXPANDER_TOKEN")
    if tok:
        env["GH_TOKEN"] = tok                       # read-only; never the state/publish PAT
    env["EXPAND_PARAMS"] = json.dumps(fork_node.get("expand", {}))
    # Nested-fork live wiring: surface the enclosing sub-pipeline's PREDECESSOR
    # sub-state evidence path (e.g. `main-review` for a `findings` fork) so an
    # expander that derives items from a prior phase's evidence can read it. Best
    # effort, nested-only, and only when the evidence actually exists — a top-level
    # fork (NODE_PATH of length 1) or a missing predecessor leaves it unset. This
    # is a computed PATH, not a secret, so it does not weaken the token allowlist.
    node_path_str = os.environ.get("NODE_PATH", "")
    if node_path_str and "." in node_path_str:
        try:
            _proto = load_protocol(proto_path)
            tp = node_path_str.split(".")
            seq_node = _paths.node_at_path(_proto, tp[:-1])
            sub_ids = [s["id"] for s in (seq_node.get("states", []) if seq_node else [])]
            if tp[-1] in sub_ids and sub_ids.index(tp[-1]) > 0:
                prev_id = sub_ids[sub_ids.index(tp[-1]) - 1]
                prev_ev = output_artifact_path(dir_, pid, instance,
                                               path=state_path(_proto, tp[:-1] + [prev_id]))
                if os.path.isfile(prev_ev):
                    env["EXPAND_PRIOR_EVIDENCE_PATH"] = prev_ev
        except Exception:
            pass  # best effort; the expander fails loud if it genuinely needs this
    try:
        r = subprocess.run([path, dir_, instance], text=True, capture_output=True,
                           env=env, timeout=hook_timeout_seconds())
    except subprocess.TimeoutExpired:
        # Fail loud, consistent with every other expander failure mode: a
        # partial/absent item list must never be treated as "no legs".
        raise ValueError(
            f"expander '{expand.get('hook')}' timed out after {hook_timeout_seconds()}s")
    if r.returncode != 0:
        raise ValueError(f"expander '{expand.get('hook')}' failed (exit {r.returncode}): {r.stderr.strip()}")
    try:
        parsed = json.loads(r.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        raise ValueError(f"expander '{expand.get('hook')}' returned non-JSON: {r.stdout[:200]!r}")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        raise ValueError(f"expander '{expand.get('hook')}' output missing 'items' array")
    return parsed["items"]


def state_by_id(protocol, state_id):
    """Return the state dict with the given id, or None."""
    for s in protocol.get("states", []):
        if s.get("id") == state_id:
            return s
    return None


def _fork_state(protocol):
    for s in protocol.get("states", []):
        if s.get("kind") == "fork":
            return s
    return None  # unchanged: still returns the FIRST top-level fork


def is_subpipeline_branch(branch_cfg):
    """True iff the branch entry is a linear sub-pipeline (has `states`)."""
    return bool(branch_cfg) and bool(branch_cfg.get("states"))


def is_dispatched_code(node):
    """True iff `node` is a `code` node running in the DISPATCHED lane (declares
    `workflow`) rather than the INLINE lane (declares `script`, run trusted in
    the plan job via `run_code_hook`). The ONE predicate for the distinction —
    every site that needs to tell the two `code` modes apart calls this rather
    than re-deriving it from `kind`/`workflow`/`script` inline, so the two
    modes cannot silently drift out of sync across next.py/advance.py/lint."""
    return node.get("kind") == "code" and bool(node.get("workflow"))


def branch_config(protocol, branch):
    """The branch entry dict from the protocol's fork state, or None."""
    fo = _fork_state(protocol)
    return _paths.child_by_id(fo.get("branches", []), branch) if fo else None


def branch_substates(protocol, branch):
    """Ordered list of sub-state dicts for a sub-pipeline branch ([] if flat)."""
    cfg = branch_config(protocol, branch)
    return list(cfg.get("states", [])) if is_subpipeline_branch(cfg) else []


def next_substate_id(protocol, branch, substate):
    """Id of the sub-state following `substate`, or None if it is the last."""
    fo = _fork_state(protocol)
    return _paths.next_sibling(protocol, [fo["id"], branch, substate]) if fo else None


def branch_output_substate(protocol, branch):
    """The last sub-state id of a sub-pipeline branch (its leg output), else None."""
    subs = branch_substates(protocol, branch)
    return subs[-1]["id"] if subs else None


def state_inputs(protocol, state_id):
    """The `inputs` list declared on a top-level state OR a branch sub-state."""
    st = state_by_id(protocol, state_id)
    if st is not None:
        return list(st.get("inputs", []))
    fo = _fork_state(protocol)
    if fo:
        for b in fo.get("branches", []):
            for s in b.get("states", []):
                if s.get("id") == state_id:
                    return list(s.get("inputs", []))
    return []


def _branch_ids(protocol):
    """Extract branch IDs from the fork state."""
    fo = _fork_state(protocol)
    return [b["id"] for b in fo.get("branches", [])] if fo else []


def _resolve_input_ref_pathaware(protocol, d, pid, instance, consuming_path, frm):
    """Path-aware (depth-4+) single-`from` resolution, nearest-scope-first
    (innermost enclosing sequence outward) relative to the consuming node's tree
    path. Walks UP the enclosing sequences; in each scope it scans the sequence's
    child states for a direct sibling match, and scans any child fork's branches
    for a nested-leg match. Returns {path, kind} or None.

      - direct sibling sub-state F → output_artifact_path(state_path(proto, scope+[F]))
        kind = 'answers' if F is a human task, else 'evidence'.
      - leg F of a child fork (scope+[forkid]) → its leg-output:
          flat leg          → state_path(proto, scope+[forkid, F])
          sub-pipeline leg  → its branch_output_substate appended.
        kind = 'evidence' (a leg output is always evidence).
    """
    scope = _paths.parent_path(consuming_path)
    while True:
        children = (_paths.children(protocol, scope) if scope
                    else protocol.get("states", []))
        for c in children:
            cid = c.get("id")
            if cid == frm:
                cpath = scope + [frm]
                kind = "answers" if _paths.node_kind(protocol, cpath) == "question" else "evidence"
                return {"path": output_artifact_path(d, pid, instance,
                                                     path=state_path(protocol, cpath),
                                                     kind=kind),
                        "kind": kind}
            if c.get("kind") == "fork":
                fo_path = scope + [cid]
                for br in c.get("branches", []):
                    if br.get("id") == frm:
                        leg_path = fo_path + [frm]
                        if is_subpipeline_branch(br):
                            last = br.get("states", [])[-1]["id"]
                            leg_path = leg_path + [last]
                        return {"path": output_artifact_path(d, pid, instance,
                                                             path=state_path(protocol, leg_path),
                                                             kind="evidence"),
                                "kind": "evidence"}
        if not scope:
            return None
        scope = _paths.parent_path(scope)


def resolve_inputs(protocol, d, pid, instance, consuming_branch, consuming_phase,
                   inputs, consuming_path=None):
    """Map each {from, as} to {as, path, kind}.

    When `consuming_path` (a tree-navigation path list) is given, resolution is
    PATH-AWARE: each `from` is resolved OUTERMOST-search relative to the consuming
    node's enclosing scopes (direct sibling sub-state, then a leg of a sibling
    nested fork, walking up to the top). This is the depth-4+ path that lets a
    nested agent's inputs reach an earlier nested-fork leg's evidence. Anything
    unresolved falls through to the legacy 3-case resolution below (so a top-level
    branch/phase `from` still works from a deep consumer).

    Legacy (consuming_path=None) resolution order for `from`:
      1) a sub-state of the consuming branch  → that sub-state's evidence
      2) a fork branch id                   → that branch's leg-output evidence
                                                 (last sub-state, or the flat leg)
      3) a phase id                           → that phase's evidence
    `kind` is 'evidence' unless the source sub-state is a human task (then 'answers').

    Depth-<=3 results (paths + kind) are BYTE-IDENTICAL to the legacy function:
    when consuming_path is None the path-aware branch is never taken."""
    phase = consuming_phase or None
    out = []
    sub_ids = {s["id"]: s for s in branch_substates(protocol, consuming_branch)} if consuming_branch else {}
    branch_ids = set(_branch_ids(protocol))
    for ref in inputs:
        frm, as_ = ref["from"], ref["as"]
        if consuming_path is not None:
            r = _resolve_input_ref_pathaware(protocol, d, pid, instance, consuming_path, frm)
            if r is not None:
                out.append({"as": as_, "path": r["path"], "kind": r["kind"]})
                continue
        if frm in sub_ids:
            kind = "answers" if sub_ids[frm].get("kind") == "question" else "evidence"
            path = output_artifact_path(d, pid, instance, branch=consuming_branch,
                                        phase=phase, substate=frm, kind=kind)
        elif frm in branch_ids:
            kind = "evidence"
            last = branch_output_substate(protocol, frm)
            path = output_artifact_path(d, pid, instance, branch=frm, phase=phase,
                                        substate=last, kind="evidence")
        else:
            path = output_artifact_path(d, pid, instance, phase=frm, kind="evidence")
            kind = "evidence"
            out.append({"as": as_, "path": path, "kind": kind})
            continue
        out.append({"as": as_, "path": path, "kind": kind})
    return out


def resolve_agent_unit_path(protocol, path):
    """Canonical: resolve the agent unit for the leaf at `path`."""
    node = _paths.node_at_path(protocol, path)
    if node is None:
        raise ValueError(f"no node at path {'.'.join(path)}")
    life = _paths.enclosing_fork_id(protocol, path)
    return {"agent_state": path[-1],
            "max_iterations": node.get("max_iterations"),
            "life_state": life if life is not None else path[-1]}


def phase_states(protocol):
    """The ordered list of 'phase' states — those of kind agent, fork,
    sequence, or code. (join and human-task states are transitions/pauses,
    not phases.)

    A `sequence` counts: it occupies a slot in the root sequence and owns a
    cursor file, so a protocol containing one is multi-phase. Without this,
    state_path would drop its leading element and collapse the group onto the
    legacy single-agent file `<pid>/<instance>.yaml`.

    A `code` node counts for the identical reason: it occupies a root-sequence
    slot and owns its own state file, exactly like a sequence (and an agent).
    Before this was added, a protocol whose root children were ALL `code`
    nodes (invisible to this function) reported `is_multiphase() == False`,
    so `state_path` dropped the leading tree-path element and EVERY node
    collapsed onto the same file `<pid>/<instance>.yaml` — silent state
    corruption, not a crash. Existing shipped protocols were safe only by
    accident (they are multi-phase via other root children); dispatched
    `code` nodes make "a pipeline of deterministic steps with no LLM" a
    natural shape to author, so the gap became reachable in practice."""
    return [s for s in protocol.get("states", [])
            if s.get("kind") in ("agent", "fork", "sequence", "code")]


def pipeline_states(protocol):
    """Ordered agent|fork|human-task states — the full human-visible pipeline.
    Used ONLY by the status renderer. phase_states() stays agent|fork so the
    agent-unit / seed / join logic is unaffected by human tasks."""
    return [s for s in protocol.get("states", [])
            if s.get("kind") in ("agent", "fork") or _paths.is_human_task(s.get("kind"))]


def is_multiphase(protocol):
    """A protocol is multi-phase iff it has more than one agent|fork phase.
    Single-phase protocols (a lone agent, or a single fork phase) keep the
    legacy layout + code paths untouched."""
    return len(phase_states(protocol)) > 1


def match_trigger(protocol, event_name, action="", comment_body="", is_pr_comment=True):
    """Map an ENTRY GitHub event to an engine command via protocol["triggers"].
    For issue_comment, a trigger's `target` (default "pr") must match whether the
    comment is on a PR (is_pr_comment True) or a plain issue (False)."""
    for t in protocol.get("triggers", []):
        if t.get("on") != event_name:
            continue
        if event_name == "issue_comment":
            want = "pr" if is_pr_comment else "issue"
            if t.get("target", "pr") != want:
                continue
            prefix = t.get("comment_prefix", "")
            if not prefix or comment_body.startswith(prefix):
                return t.get("command", "")
        elif event_name == "pull_request":
            actions = t.get("actions", [])
            if not actions or action in actions:
                return t.get("command", "")
        else:
            # generic event (e.g. workflow_dispatch): match on `on` alone.
            return t.get("command", "")
    return ""


def command_prefix(protocol, command, default=""):
    """Return the `comment_prefix` of the first trigger that maps to `command`,
    or `default` if no such trigger declares one. Lets the engine strip the
    protocol-configured prefix (e.g. /answer, /clarify) from a command's comment
    body instead of a hardcoded literal — so the answer-comment syntax stays
    per-protocol, not coupled to any one protocol's chosen verb."""
    for t in protocol.get("triggers", []):
        if t.get("command") == command and t.get("comment_prefix"):
            return t["comment_prefix"]
    return default


def route(protocols_dir, event_name, action="", comment_body="",
          dispatch_protocol="", is_pr_comment=True):
    """Pick the protocol to run for an incoming event by scanning all
    protocols/*/protocol.json `triggers` blocks. Protocol-agnostic router core.

    Returns {"protocol": <path>, "command": <cmd>, "skip": <bool>}:
      - repository_dispatch (dispatch_protocol set): the dispatch carries the
        protocol NAME (advance.py sends pid; protocol-join.yml rebuilds the path
        the same way), so reconstruct <protocols_dir>/<name>/protocol.json — the
        engine needs a path to open. No scan; command re-derived from the type.
      - entry event (pull_request / issue_comment): glob protocols in sorted
        order, run match_trigger on each (forwarding is_pr_comment so a comment's
        trigger `target` pr/issue must match a PR vs a plain issue); 0 matches ->
        skip, exactly 1 -> route, >=2 -> raise ValueError (ambiguous; the router
        job then fails loudly).
    """
    if dispatch_protocol:
        return {"protocol": os.path.join(protocols_dir, dispatch_protocol, "protocol.json"),
                "command": "", "skip": False}
    matches = []
    for path in sorted(glob.glob(os.path.join(protocols_dir, "*", "protocol.json"))):
        proto = load_protocol(path)
        cmd = match_trigger(proto, event_name, action, comment_body,
                            is_pr_comment=is_pr_comment)
        if cmd:
            matches.append((path, cmd))
    if not matches:
        return {"protocol": "", "command": "", "skip": True}
    if len(matches) > 1:
        names = ", ".join(p for p, _ in matches)
        # Describe WHAT collided in the trigger's own terms, not the raw GitHub
        # event/action (e.g. "issue_comment/created" hides that the comment text
        # "/review" is the thing two protocols both matched).
        if event_name == "issue_comment":
            what = f'the comment "{comment_body}"'
        elif event_name == "pull_request":
            what = f'pull_request action "{action}"'
        else:
            what = f'event "{event_name}"'
        raise ValueError(
            f"ambiguous route: {what} matches {len(matches)} protocols "
            f"({names}); their triggers overlap - make them mutually exclusive "
            f"(no comment_prefix may be a prefix of another protocol's)")
    path, cmd = matches[0]
    return {"protocol": path, "command": cmd, "skip": False}


def pr_from_instance(instance):
    """Derive the PR/issue NUMBER from an instance key.
    pr-<N> and issue-<N> -> <N> (the GitHub thread number, numeric so the engine
    can comment/label on it). ref-*/ui-* and any other shape pass through verbatim
    (no numeric thread)."""
    for prefix in ("pr-", "issue-"):
        if instance.startswith(prefix):
            return instance[len(prefix):]
    return instance


def instance_file(d, pid, instance):
    """instance_file <dir> <protocol-id> <instance-key> — shared per-instance bookkeeping."""
    return f"{d}/{pid}/{instance}/_instance.yaml"


def issue_question_body(pid, instance, human_task_id, questions):
    """The body of an issue-channel `question` node's issue: a machine marker (so the
    answer comment can be routed back to this run) + a parseable YAML block of the
    questions (for a UI to render) + the /answer instructions."""
    marker = f"<!-- agentic-mm: protocol={pid} instance={instance} human_task={human_task_id} -->"
    qlines = "\n".join(f"  - id: {q['id']}\n    text: {json.dumps(q['text'])}" for q in questions)
    eg = questions[0]["id"] if questions else "q1"
    return (
        f"{marker}\n\n"
        f"## Open questions — answer to resume mental-model recovery (`{instance}`)\n\n"
        f"```yaml\nquestions:\n{qlines}\n```\n\n"
        f"Reply with one or more `/answer <id>: <value>` lines in a single comment, "
        f"e.g. `/answer {eg}: …`. The run resumes automatically and this issue is "
        f"closed once every question is answered."
    )


def _publish_enclosing_unit(dir_, pid, instance, proto_path, tree_path, sha, summary):
    """Publish the `in_progress` check-run of the unit that CONTAINS an open
    human task. A human task is a STEP, not a publishing unit — it never gets a
    check-run of its own — so what pends here is the enclosing sequence / fork
    leg (the root, for a root-level approval). Needs the human task's TREE path:
    the file-naming path is not a coordinate (state_path drops the leading phase
    id on a single-phase protocol, so its parent is not the enclosing unit).

    `summary` is the instruction the pending unit owes the reader -- how to
    answer or approve. It is the whole reason a human looks at this check-run,
    so it is a required argument, not an optional flourish.

    Deliberately does NOT swallow a failure to read the protocol: an unreadable
    protocol.json means the human task just opened with NOTHING pending on the
    merge box, and a silent `return` here would make that look like a run with no
    human task. Fail loudly -- the caller has already written the human-task state
    file, so an operator can see exactly where it stopped."""
    proto = load_protocol(proto_path)
    unit = list(_paths.completing_scope(proto, tree_path)[1]) if tree_path else []
    publish_check_run(dir_, pid, instance, proto, unit, sha, status="in_progress",
                      summary=summary)


def open_human_task(dir_, pid, instance, proto_path, human_task_id, sha, pr, branch=None, questions=None,
              phase=None, path=None, channel="comment", tree_path=None):
    """Seed a human-task state file (human_task.state=open), emit the awaiting check-run, and
    refresh the status comment. `branch` scopes the human task to a sub-pipeline leg.
    `phase` qualifies the path for multi-phase fan-out legs (e.g. review.B.clarify.yaml).
    `path` is the canonical FILE-NAMING path (already converted via state_path); when
    given it takes precedence over branch/phase/human_task_id for the state file and check-run
    name. `questions` (a list of {id,text}) turns this into a data-carrying human task whose
    comment lists them with the /answer syntax. `channel="issue"` opens a dedicated
    GitHub issue (for ref/UI-keyed runs that have no PR) instead of posting to `pr`,
    and records its number on `human_task.issue`. `tree_path` is the human task's
    TREE path, used to find the enclosing publishing unit whose check-run pends
    (the file-naming `path` cannot answer that: it is not a coordinate).
    Caller owns the cursor + cas_push."""
    if path is not None:
        sf = state_file(dir_, pid, instance, path=path)
    elif branch:
        sf = state_file(dir_, pid, instance, branch=branch, substate=human_task_id, phase=phase)
    else:
        sf = state_file(dir_, pid, instance, phase=human_task_id)
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    human_task = {"state": "open", "history": []}
    if questions:
        human_task["questions"] = questions
    if questions and channel == "issue":
        # Interactive (no-PR) human task: open a dedicated question issue; the answer
        # comment is routed back via the marker in its body (mm-interactive-resume.yml).
        num = create_issue(f"Mental model — open questions ({instance})",
                           issue_question_body(pid, instance, human_task_id, questions))
        human_task["channel"] = "issue"
        if num:
            human_task["issue"] = num
        dump_yaml(sf, {"protocol": pid, "instance": instance, "state": human_task_id,
                       "head_sha": sha, "human_task": human_task})
        _publish_enclosing_unit(
            dir_, pid, instance, proto_path, tree_path or [human_task_id], sha,
            f"Answer the questions on issue #{num or '(created)'} "
            f"with `/answer <id>: <value>`.")
        return
    dump_yaml(sf, {
        "protocol": pid, "instance": instance, "state": human_task_id,
        "head_sha": sha, "human_task": human_task,
    })
    if questions:
        # Use the protocol's CONFIGURED answer-command prefix (e.g. /mm-answer), not a
        # hardcoded /answer — do_answer strips that same per-protocol prefix, so a human task
        # whose protocol registers a non-/answer verb would otherwise instruct a command
        # that routes to nothing and the human task would sit forever.
        try:
            ans = command_prefix(load_protocol(proto_path), "answer", "/answer")
        except (OSError, ValueError):
            ans = "/answer"
        listed = "\n".join(f"{i+1}. `{q['id']}` — {q['text']}" for i, q in enumerate(questions))
        summary = (f"Answer with `{ans} <id>: <value>` (one or more per comment), e.g. "
                   f"`{ans} {questions[0]['id']}: …`.")
        _publish_enclosing_unit(dir_, pid, instance, proto_path,
                                tree_path or [human_task_id], sha, summary)
        post_pr_comment(pr, f"❓ **{human_task_id}** needs input:\n\n{listed}\n\n{summary}")
    else:
        _publish_enclosing_unit(
            dir_, pid, instance, proto_path, tree_path or [human_task_id], sha,
            "Comment `/approve`, `/request-changes`, or `/reject` on this PR.")
    inf = instance_file(dir_, pid, instance)
    if os.path.isfile(inf):
        body = render_pipeline_status_body(dir_, pid, instance, proto_path)
        upsert_status_comment(inf, pr, body)


def state_checkout(dir_):
    """state_checkout <dir> — clone the state branch; create it on origin if missing."""
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", STATE_REMOTE, STATE_BRANCH],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        subprocess.run(
            ["git", "clone", "-q", "--branch", STATE_BRANCH, "--single-branch", STATE_REMOTE, dir_],
            check=True, text=True
        )
    else:
        subprocess.run(["git", "init", "-q", "--initial-branch", STATE_BRANCH, dir_], check=True, text=True)
        git(dir_, "remote", "add", "origin", STATE_REMOTE)
        git(dir_, *GIT_ID, "commit", "-q", "--allow-empty", "-m", "init agentic-state")
        git(dir_, "push", "-q", "origin", STATE_BRANCH)


def cas_push(dir_, msg, attempts=12):
    """Commit everything and push fast-forward-only, retrying via rebase up to
    `attempts` times. NEVER force-push. A genuinely empty commit is a bug → fail.

    The retry budget is sized for FAN-OUT CONTENTION, which is the only place
    this loop matters. A fork advances N legs concurrently and every one writes
    its own file to the same state branch, so N-1 of them are rejected on the
    first try by construction. A writer that exhausts its attempts exits 1 and
    its verdict is never recorded — the leg's cursor never moves, the fork's
    join waits forever on a leg that can no longer change, and the run parks
    with nothing to show for it but one red `advance` job.

    That is not hypothetical: on PR 215 seven per-issue legs finished triage
    within the same second, six landed, and the seventh lost all five attempts
    (run 32157211726, job 95786992234). The old schedule was 5 tries with
    0.1/0.2/0.3/0.4s of sleep — about ONE SECOND of patience for a race where
    each contender holds the ref for a fetch+rebase+push.

    Two changes, and both are needed:

    * `attempts` must exceed the leg count, since each rejection means another
      writer won. 12 covers the widest fan-out this protocol produces with
      headroom; a fork wider than that would need more.
    * the backoff is EXPONENTIAL and JITTERED. Fixed delays gave every loser
      the same cadence, so the same contenders re-collided in lockstep —
      spreading them out is what actually breaks the tie, not just waiting
      longer.
    """
    import random
    import time
    git(dir_, *GIT_ID, "add", "-A")
    # An empty commit here means the engine pushed without changing state — a bug; fail loudly.
    staged = subprocess.run(["git", "-C", dir_, "diff", "--cached", "--quiet"]).returncode
    if staged == 0:
        sys.stderr.write("[engine] cas_push: nothing staged — refusing empty commit\n")
        sys.exit(1)
    git(dir_, *GIT_ID, "commit", "-q", "-m", msg)
    for i in range(attempts):
        r = subprocess.run(["git", "-C", dir_, "push", "-q", "origin", STATE_BRANCH])
        if r.returncode == 0:
            return
        sys.stderr.write(f"[engine] CAS push rejected (attempt {i+1}/{attempts}), rebasing\n")
        git(dir_, *GIT_ID, "pull", "-q", "--rebase", "origin", STATE_BRANCH)
        if i + 1 < attempts:
            # Exponential, capped, with full jitter over [0, backoff]. The cap
            # keeps a long tail bounded; the jitter is what de-synchronises
            # contenders that collided on the previous round.
            backoff = min(0.25 * (2 ** i), 8.0)
            time.sleep(random.uniform(0.0, backoff))
    sys.stderr.write("[engine] CAS push failed after retries\n")
    sys.exit(1)


def resolve_executable(sdir, name, pdir, ex=""):
    """
    resolve_executable <search-dir> <name> <protocol-dir> <explicit-exec-or-empty>
    Prints OK\t<path> or ERR\t<reason> to stdout.
    """
    if ex:
        path = f"{pdir}/{ex}"
        if os.path.isfile(path):
            return f"OK\t{path}"
        else:
            return f"ERR\tdeclared exec not found: {ex}"

    # Extension-agnostic: match <sdir>/<name> or <sdir>/<name>.*
    matches = []
    exact = f"{sdir}/{name}"
    if os.path.isfile(exact):
        matches.append(exact)
    # glob for extensions
    for g in sorted(glob.glob(f"{sdir}/{name}.*")):
        if os.path.isfile(g):
            matches.append(g)

    if len(matches) == 0:
        return f"ERR\tno executable found (looked for {sdir}/{name} or {sdir}/{name}.*)"
    elif len(matches) > 1:
        return f"ERR\tambiguous: multiple files match {sdir}/{name}.* ({' '.join(matches)}); use an explicit \"exec\""
    else:
        return f"OK\t{matches[0]}"


# --- GitHub write helpers: token-pool rotation -----------------------------
# Engine WRITE calls (comments, check-runs, dispatches) run in ~25 isolated,
# parallel Actions jobs that share no process memory — so a "current token"
# pointer cannot be global. Instead every job carries the FULL token pool and
# fails over LOCALLY: on a 403/429 rate-limit it moves to the next token
# (first -> _2 -> …). Failover is ALWAYS on. When a full lap finds every token
# exhausted it FAILS FAST by default (GH_ROTATE_MAX_WAIT_S=0); set that > 0 to opt
# into cycling BACK to the first token with a bounded wait (a token's window
# resets while we pause). The pool is PUBLISH_TOKEN + PUBLISH_TOKEN_2 …
# PUBLISH_TOKEN_9 (each wired to a distinct dispatch-PAT secret, e.g.
# POC_DISPATCH_TOKEN / POC_DISPATCH_TOKEN_2); with only PUBLISH_TOKEN set and the
# default fail-fast, a rate-limited write behaves as before (one call, then fail/log).
def _publish_tokens():
    """Ordered write-token pool: PUBLISH_TOKEN, then PUBLISH_TOKEN_2 … PUBLISH_TOKEN_9,
    each wired (in the workflow) to a distinct dispatch-PAT secret. Unset/blank
    entries are skipped; with only PUBLISH_TOKEN set this is a single token (no
    rotation), unchanged from before."""
    toks = []
    primary = os.environ.get("PUBLISH_TOKEN", "").strip()
    if primary:
        toks.append(primary)
    for i in range(2, 10):  # PUBLISH_TOKEN_2 .. PUBLISH_TOKEN_9
        val = os.environ.get(f"PUBLISH_TOKEN_{i}", "").strip()
        if val and val not in toks:
            toks.append(val)
    return toks


def _token_identity(token):
    """`(login, remaining, limit, reset_epoch)` for one pool token, or None.

    NEVER raises and never returns the token itself. `gh api rate_limit` does not
    consume quota, so this is free to call.

    `login` is why this exists at all. GitHub's SECONDARY limits are per-USER,
    not per-token, so two PATs minted on the same account share one secondary
    budget and rotating between them buys nothing against the failure we
    actually hit. A pool whose logins are all identical is a pool of one, and
    only the token's own `/user` can say so.
    """
    env = dict(os.environ)
    for _k in _POOL_ENV_KEYS:
        env.pop(_k, None)
    if token:
        env["GH_TOKEN"] = token
    try:
        who = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                             text=True, capture_output=True, env=env, timeout=30)
        login = who.stdout.strip() if who.returncode == 0 else None
        rl = subprocess.run(["gh", "api", "rate_limit", "--jq",
                             ".resources.core | \"\\(.remaining) \\(.limit) \\(.reset)\""],
                            text=True, capture_output=True, env=env, timeout=30)
        if rl.returncode != 0:
            return (login, None, None, None)
        remaining, limit, reset = rl.stdout.split()
        return (login, int(remaining), int(limit), int(reset))
    except Exception:
        return None


def log_token_pool(stream=None):
    """Log each pool token's owner and core-quota headroom. NEVER raises.

    Diagnostic only — it changes no behaviour and is never on a write path. It
    exists because the two failures that actually cost us runs were both
    invisible until the moment rotation was needed: a token that had silently
    EXPIRED (rotation reached it and got `Bad credentials`, killing the run),
    and a pool whose members shared one account (so rotation could not relieve a
    secondary limit). Both are answerable up front, and neither is answerable
    from outside CI — only a job holding the secrets can ask.

    Prints one line per token: index, owner login, remaining/limit, and the
    reset time as an ABSOLUTE UTC timestamp rather than a countdown, so a line
    read in a log hours later still means something. A token that cannot
    authenticate is called out as INVALID, which is the whole point: that is the
    state that reads as healthy right up until it isn't.
    """
    out = stream if stream is not None else sys.stderr
    try:
        toks = _publish_tokens()
        if not toks:
            out.write("[engine] token pool: empty (ambient GH_TOKEN only)\n")
            return
        logins = []
        for i, tok in enumerate(toks, start=1):
            name = "PUBLISH_TOKEN" if i == 1 else f"PUBLISH_TOKEN_{i}"
            ident = _token_identity(tok)
            if ident is None or ident[0] is None:
                out.write(f"[engine] token pool {i}/{len(toks)} {name}: "
                          "INVALID — cannot authenticate (expired or revoked?); "
                          "rotation onto this token will fail the write\n")
                continue
            login, remaining, limit, reset = ident
            logins.append(login)
            if remaining is None:
                out.write(f"[engine] token pool {i}/{len(toks)} {name}: "
                          f"owner={login} quota=unavailable\n")
                continue
            when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset))
            out.write(f"[engine] token pool {i}/{len(toks)} {name}: owner={login} "
                      f"core={remaining}/{limit} resets={when}\n")
        # The pool's REAL depth against a secondary limit is its distinct-login
        # count, not its length.
        if len(toks) > 1 and len(set(logins)) == 1:
            out.write(f"[engine] token pool: all {len(toks)} tokens belong to "
                      f"{logins[0]} — GitHub's SECONDARY limits are per-user, so "
                      "rotation cannot relieve one; mint a pool token on another "
                      "account\n")
    except Exception:
        pass


def _looks_rate_limited(result):
    """True when a failed `gh api` result is a GitHub rate-limit (worth rotating).
    Deliberately narrow: a permission 403 is NOT a rate-limit and must not rotate.
    Scans stdout too, and matches the legacy "abuse detection" wording."""
    if result is None or result.returncode == 0:
        return False
    msg = ((result.stderr or "") + " " + (result.stdout or "")).lower()
    return ("rate limit" in msg or "secondary rate" in msg
            or "abuse detection" in msg
            or "http 429" in msg or "429 too many" in msg)


_POOL_ENV_KEYS = ("PUBLISH_TOKEN", "PUBLISH_TOKENS") + tuple(
    f"PUBLISH_TOKEN_{i}" for i in range(2, 10))


def _rotate_float(name, default):
    """Parse a GH_ROTATE_* seconds value; fall back to `default` for a missing,
    blank, non-numeric, non-finite, or negative value — so a misconfigured env var
    can never crash a write or make the wait budget unbounded."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if math.isfinite(val) and val >= 0 else default


def run_gh_rotating(api_args, *, tokens=None, check=False):
    """`gh api <api_args>` that CYCLES the token pool on a rate-limit (403/429):
    PUBLISH_TOKEN -> PUBLISH_TOKEN_2 -> … -> back to the first, and around again.

    When a full lap finds EVERY token rate-limited it FAILS FAST by default
    (`GH_ROTATE_MAX_WAIT_S=0`). Set that > 0 to opt into cycling BACK to the first
    token: it waits `GH_ROTATE_WAIT_S` (default 60s) and laps again, up to
    `GH_ROTATE_MAX_WAIT_S` total, so a token's hourly window can reset while it
    pauses. Failover between tokens is always on; a *permission* 403 (not a
    rate-limit) never rotates.

    Each isolated job runs this independently (no shared state). Returns the first
    success (or the last attempt); raises CalledProcessError with check=True if it
    ultimately fails. An empty pool is one plain `gh api` on the ambient GH_TOKEN.
    """
    toks = tokens if tokens is not None else _publish_tokens()
    if not toks:
        toks = [""]  # inherit ambient GH_TOKEN
    n = len(toks)
    wait_s = _rotate_float("GH_ROTATE_WAIT_S", 60.0)
    max_wait_s = _rotate_float("GH_ROTATE_MAX_WAIT_S", 0.0)  # 0 = fail fast; opt in to waiting
    waited = 0.0
    attempt = 0
    result = None
    while True:
        tok = toks[attempt % n]
        # Give the child only the token it needs — scrub the sibling pool secrets
        # from its environment so a `gh` subprocess never sees the other tokens.
        env = dict(os.environ)
        for _k in _POOL_ENV_KEYS:
            env.pop(_k, None)
        if tok:
            env["GH_TOKEN"] = tok
        result = subprocess.run(["gh", "api"] + list(api_args),
                                text=True, capture_output=True, env=env)
        if result.returncode == 0:
            return result
        if not _looks_rate_limited(result):
            break  # a real (non-rate-limit) error — do not rotate
        attempt += 1
        if attempt % n != 0:
            sys.stderr.write(
                f"[engine] gh token rate-limited; switching to token {attempt % n + 1}/{n}\n")
            continue  # tokens left this lap — try the next one immediately
        # full lap done: every token is rate-limited right now
        pause = min(wait_s, max_wait_s - waited) if wait_s > 0 else 0.0
        if pause <= 0:
            break  # no (more) wait budget — give up rather than spin
        sys.stderr.write(
            f"[engine] all {n} token(s) rate-limited; waiting {pause:.0f}s, "
            f"then retrying from token 1\n")
        time.sleep(pause)
        waited += pause
    if check and (result is None or result.returncode != 0):
        raise subprocess.CalledProcessError(
            result.returncode if result else 1,
            ["gh", "api"] + list(api_args),
            output=result.stdout if result else "",
            stderr=result.stderr if result else "")
    return result


def set_check_run(name, sha, status, conclusion, title, summary):
    """
    set_check_run <name> <head_sha> <status> <conclusion-or-empty> <title> <summary>
    Best-effort: failure never breaks a transition.
    """
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(
            f"[ENGINE_LOCAL] check-run {name} sha={sha} status={status} "
            f"conclusion={conclusion or 'none'} title={title} summary={summary}\n"
        )
        return
    if not sha:
        sys.stderr.write("[engine] no head sha; skipping check run\n")
        return
    args = [
        "-f", f"name={name}",
        "-f", f"head_sha={sha}",
        "-f", f"status={status}",
        "-f", f"output[title]={title}",
        "-f", f"output[summary]={summary}",
    ]
    if conclusion:
        args += ["-f", f"conclusion={conclusion}"]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    # Check-runs must be created by the ACTIONS token (github-actions[bot]) — a
    # classic PAT cannot create/supersede an Actions-app check-run. Prefer a
    # dedicated CHECK_RUN_TOKEN (the workflow's GITHUB_TOKEN, which the job grants
    # `checks: write`); fall back to PUBLISH_TOKEN for callers whose PUBLISH_TOKEN
    # already IS the Actions token (advance/join jobs). This matters for a protocol
    # that finalizes at a terminal `merge` in the plan job, where PUBLISH_TOKEN is
    # the dispatch PAT (which can post the review but cannot complete the check-run).
    # Prefer the single Actions CHECK_RUN_TOKEN (only it can create an Actions-app
    # check-run). When it is absent the caller falls back to the classic-PAT pool,
    # which rotates so a rate-limited PAT fails over instead of dropping the check.
    check_token = os.environ.get("CHECK_RUN_TOKEN", "")
    if check_token:
        env = dict(os.environ)
        env["GH_TOKEN"] = check_token
        result = subprocess.run(
            ["gh", "api", "-X", "POST", f"repos/{repo}/check-runs"] + args,
            text=True, capture_output=True, env=env
        )
    else:
        result = run_gh_rotating(["-X", "POST", f"repos/{repo}/check-runs"] + args)
    if result.returncode != 0:
        sys.stderr.write(
            "[engine] check-run create failed (needs checks:write + Actions token; "
            f"merge-gating needs branch protection): {result.stderr.strip()}\n"
        )


# --- Phase labels -----------------------------------------------------------
# Engine-level head keys that are NOT protocol states. Protocols may override
# any of these via a top-level "phase_labels" map in protocol.json.
PHASE_LABEL_DEFAULTS = {
    "setup": "⚙ setup",
    "done": "✅ done",
    "failed": "❌ failed",
    "blocked": "⛔ blocked",
}
PHASE_LABEL_COLOR = "5319e7"  # one color for every engine-managed phase label


def _humanize_state_id(state_id):
    return state_id.replace("-", " ").replace("_", " ").strip().capitalize()


def phase_label_text(protocol, key):
    """Resolve a state id OR a terminal/special key to a PR label string.

    Live phase (key matches a states[] id): the state's `label` if present, else
    a humanized id. Terminal/special key (setup/done/failed/blocked): the
    protocol's optional top-level `phase_labels[key]` override if present, else
    the engine default. `protocol` is the parsed protocol JSON dict.
    """
    st = state_by_id(protocol, key)
    if st is not None:
        return st.get("label") or _humanize_state_id(key)
    overrides = protocol.get("phase_labels", {}) or {}
    if key in overrides:
        return overrides[key]
    return PHASE_LABEL_DEFAULTS.get(key, _humanize_state_id(key))


def _gh_label_cmd(args):
    """Run a best-effort `gh` command for labels/PR-edit. Returns (ok, stderr).
    Never raises. Uses PUBLISH_TOKEN (as GH_TOKEN) + GITHUB_REPOSITORY."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    env = dict(os.environ)
    token = os.environ.get("PUBLISH_TOKEN", "")
    if token:
        env["GH_TOKEN"] = token
    try:
        result = subprocess.run(
            ["gh"] + args + (["--repo", repo] if repo else []),
            text=True, capture_output=True, env=env,
        )
        return result.returncode == 0, result.stderr
    except Exception as e:  # gh missing, etc. — never break a transition
        return False, str(e)


def _ensure_and_add_label(text, pr):
    """Ensure the label exists (idempotent --force create) then add it to the PR.
    Best-effort. ENGINE_LOCAL → log only."""
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] add-label pr={pr}: {text}\n")
        return
    if not str(pr).isdigit():   # ref-/UI-targeted run: no PR to label
        return
    # gh pr edit --add-label errors on a nonexistent label, so create-first.
    _gh_label_cmd(["label", "create", text, "--color", PHASE_LABEL_COLOR, "--force"])
    ok, err = _gh_label_cmd(["pr", "edit", str(pr), "--add-label", text])
    if not ok:
        sys.stderr.write(f"[engine] add-label failed for '{text}': {err}\n")


def remove_pr_label(pr, label):
    """Best-effort remove one label from the PR. ENGINE_LOCAL → log only."""
    if not label:
        return
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] remove-label pr={pr}: {label}\n")
        return
    if not str(pr).isdigit():   # ref-/UI-targeted run: no PR to label
        return
    _gh_label_cmd(["pr", "edit", str(pr), "--remove-label", label])


def apply_setup_label(protocol, pr):
    """Add the engine 'setup' label to the PR. Best-effort, no state tracking —
    called before _instance.yaml exists. ensure_phase_label removes it later."""
    _ensure_and_add_label(phase_label_text(protocol, "setup"), pr)


def ensure_phase_label(dir_, pid, instance, protocol, pr, head_key):
    """Reconcile the PR's phase label to `head_key`.

    Reads the applied label from _instance.yaml; if it differs from the resolved
    new text, removes {prev} ∪ {setup-label} and adds the new one; records the
    new text back on _instance.yaml. No-op when there is no _instance.yaml (this
    excludes the single-agent v1 path). Best-effort. ENGINE_LOCAL → log + still
    record state. The CALLER cas_pushes the instance file."""
    inf = instance_file(dir_, pid, instance)
    if not os.path.isfile(inf):
        return
    try:
        inst = load_yaml(inf) or {}
        new = phase_label_text(protocol, head_key)
        prev = inst.get("phase_label", "") or ""
        if prev == new:
            return
        setup_text = phase_label_text(protocol, "setup")
        if os.environ.get("ENGINE_LOCAL", "0") == "1":
            sys.stderr.write(f"[ENGINE_LOCAL] phase-label {instance}: {prev or '∅'} → {new}\n")
            inst["phase_label"] = new
            dump_yaml(inf, inst)
            return
        for old in {prev, setup_text}:
            if old and old != new:
                remove_pr_label(pr, old)
        _ensure_and_add_label(new, pr)
        inst["phase_label"] = new
        dump_yaml(inf, inst)
    except Exception as e:
        sys.stderr.write(f"[engine] ensure_phase_label failed (non-fatal): {e}\n")


def match_run_by_cid(runs_json, cid):
    """
    match_run_by_cid <runs-json> <cid>
    Pure resolver: finds the databaseId whose displayTitle contains the delimited
    token "cid:[<cid>]". Returns the id as a string, or empty string if none match.
    """
    needle = f"cid:[{cid}]"
    try:
        runs = json.loads(runs_json)
    except json.JSONDecodeError:
        return ""
    for run in runs:
        title = run.get("displayTitle") or ""
        if needle in title:
            return str(run["databaseId"])
    return ""


def join_policy_satisfied(policy, done, total):
    """Is a dynamic join's barrier satisfied given `done` legs out of `total`?
      all (default) : every leg done (vacuously true when total==0)
      any           : >=1 leg done (false when total==0)
      quorum:N      : >=N done, N an int count OR a percentage of total ('80%')
    Raises ValueError on an unparseable quorum."""
    policy = (policy or "all").strip()
    if policy == "all":
        return done == total
    if policy == "any":
        return done >= 1
    if policy.startswith("quorum:"):
        spec = policy[len("quorum:"):].strip()
        if spec.endswith("%"):
            try:
                pct = float(spec[:-1])
            except ValueError:
                raise ValueError(f"unparseable quorum percentage: {policy!r}")
            need = math.ceil(total * pct / 100.0)
        else:
            try:
                need = int(spec)
            except ValueError:
                raise ValueError(f"unparseable quorum count: {policy!r}")
        return done >= need
    raise ValueError(f"unknown join policy: {policy!r}")


def decide(results, iterations_remaining, *, checks_declared=True):
    """Pure fold: (check verdicts + severities) → (process, blocking).

    process  ∈ {"done","iterate","failed"} — the process axis that drives the
             iterate loop and terminal state.
    blocking : bool — did a `block`-severity check fail (the conclusion-axis
             input; folded with a node's `conclude` result by `block_exit`).

    Severity is each verdict's "on_fail" (default "iterate" when absent, so
    pre-severity verdicts and the single-agent regression path are unchanged).
    `iterate`-severity failures drive the loop; `block` failures never iterate
    but set blocking; `advisory` failures are recorded only.

    Zero verdicts is ambiguous on its own, so the caller disambiguates via
    `checks_declared`: when checks WERE declared for this node but produced no
    verdicts, that is a checks-job infrastructure failure → treated as a
    failed attempt (iterate/failed per `iterations_remaining`, unchanged
    default behavior). When NO checks were declared at all, there is nothing
    to fold — a legitimately successful step (e.g. a dispatched `code` node
    with no `checks[]`) must not loop to `failed` for lack of something to
    check, so this returns `("done", False)`.

    Callers must stamp `on_fail` onto each verdict from the protocol's check
    entry before calling (see run-checks.py); absent it, every failure defaults
    to `iterate` (v1 behavior).
    """
    if not results:
        if not checks_declared:
            return "done", False
        return ("iterate" if iterations_remaining else "failed"), False
    def sev(v):
        return v.get("on_fail", "iterate")
    iterate_fail = any(not v.get("pass") and sev(v) == "iterate" for v in results)
    block_fail = any(not v.get("pass") and sev(v) == "block" for v in results)
    if iterate_fail:
        process = "iterate" if iterations_remaining else "failed"
    else:
        process = "done"
    return process, block_fail


def node_outcome(dir_, pid, instance, file_path, exit_code, summary=""):
    """Record ONE step's exit status into its own state file.

    `exit_code` is the single signal in the 4.0.0 model: 0 = the step is content,
    nonzero = it objects. Whether objecting also STOPS the run is a separate,
    declared concern (`on_blocked: "halt"` on the node) -- colour is not flow.
    `file_path` is the FILE-NAMING path (already through state_path).
    """
    sf = state_file(dir_, pid, instance, path=file_path)
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    data = load_yaml(sf) if os.path.isfile(sf) else {}
    data["outcome"] = {"exit": int(exit_code), "summary": summary or ""}
    dump_yaml(sf, data)


def sequence_outcome(dir_, pid, instance, proto, unit_path):
    """Fold every step under `unit_path` into (worst_exit, first_nonzero_summary).

    A step that recorded NO outcome counts as 0: an agent with no `conclude` had
    nothing evaluate its evidence, so nothing objected. Absent is green, which is
    also what keeps every flat leg declaring no `conclude` working unchanged.

    Recurses into nested sequences but NOT into a nested fork's legs -- each leg
    publishes its own check-run, so its colour is its own.

    The EMPTY path is the ROOT sequence (the aggregate gating check-run). It has
    to be spelled out because `paths.node_at_path(proto, [])` is None -- the root
    is the protocol document itself, not a node -- so `paths.children` returns
    nothing for it. Folding no children would have made the aggregate green
    unconditionally, which is precisely the check the merge box depends on.
    """
    worst, summary = 0, ""
    kids = _paths.children(proto, unit_path) if unit_path else (proto.get("states") or [])
    if unit_path and not kids and _paths.node_kind(proto, unit_path) not in ("sequence", "fork"):
        # A BARE fork leg -- an `agent`/`code` node used directly as a branch. It
        # is still a publishing unit ("a job with exactly one step"), and that one
        # step is the node ITSELF, which has no children to iterate. Folding its
        # (empty) child list returned green and threw away the leg's own exit
        # status -- the very signal `conclude` and `on_fail: "block"` produce on
        # the flat legs of recover-mental-model and code-review.
        sf = state_file(dir_, pid, instance, path=state_path(proto, unit_path))
        oc = (load_yaml(sf).get("outcome") or {}) if os.path.isfile(sf) else {}
        return int(oc.get("exit", 0) or 0), oc.get("summary", "") or ""
    for child in kids or []:
        cid = child.get("id")
        if not cid:
            continue
        cpath = list(unit_path) + [cid]
        if _paths.node_kind(proto, cpath) == "fork":
            continue                      # each leg owns its own check-run
        if _paths.is_sequence(proto, cpath):
            e, s = sequence_outcome(dir_, pid, instance, proto, cpath)
        else:
            sf = state_file(dir_, pid, instance, path=state_path(proto, cpath))
            oc = (load_yaml(sf).get("outcome") or {}) if os.path.isfile(sf) else {}
            e, s = int(oc.get("exit", 0) or 0), oc.get("summary", "") or ""
        if e:
            if not worst:
                worst, summary = e, s
            else:
                worst = max(worst, e)
                # Prefer non-empty summary: if current summary is empty but new one is not, take it
                if not summary and s:
                    summary = s
    return worst, summary


def check_run_name(pid, unit_path):
    """The GitHub check-run name for a publishing unit. The ROOT (empty path) is
    the protocol id -- the aggregate gating check-run (HOW-IT-WORKS.md 5.1)."""
    return pid if not unit_path else pid + "/" + "/".join(unit_path)


def humanize(node_id):
    """A node id rendered for humans: 'preflight-verdict' -> 'Preflight verdict'."""
    return (node_id or "").replace("-", " ").replace("_", " ").strip().capitalize()


def publish_check_run(dir_, pid, instance, proto, unit_path, sha, status="completed",
                      summary=""):
    """Publish THE check-run for one publishing unit (a sequence, or a fork leg).

    THE single publication seam. Every check-run the engine emits goes through
    here; `tests/engine/test_check_run_publication.py` asserts by AST scan that
    no other site calls set_check_run. Before 4.0.0 there were 26 such sites,
    with code-review's vocabulary (`Review complete`, `Approved`) compiled into
    the generic engine.

    Colour is the fold of the unit's steps: nonzero anywhere -> failure. The
    title comes from the unit's own `label` (or a humanized id), never from the
    engine. Summary text is the first objecting step's, REDACTED -- it reaches a
    public check-run and may quote hook stderr.

    `summary` applies ONLY to a non-completed status, where there is no fold to
    take it from: it carries the instruction a pending unit owes the reader
    ("Comment `/approve`...", "Answer the questions on issue #N..."). A completed
    unit's summary always comes from the fold -- passing one would be a caller
    overriding what the steps actually said.
    """
    node = _paths.node_at_path(proto, unit_path) if unit_path else None
    title = (node or {}).get("label") or (humanize(unit_path[-1]) if unit_path else pid)
    if status != "completed":
        set_check_run(check_run_name(pid, unit_path), sha, status, "", title,
                      _redact(summary))
        return
    exit_code, summary = sequence_outcome(dir_, pid, instance, proto, unit_path)
    set_check_run(check_run_name(pid, unit_path), sha, "completed",
                  "failure" if exit_code else "success", title, _redact(summary))


def upsert_status_comment(sf, pr, body):
    """
    upsert_status_comment <state_file> <pr> <body>
    Single engine-owned PR comment, edited in place; id persisted in state.
    Mutates the state file but does NOT push.
    """
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] status comment pr#{pr}: {body}\n")
        return
    # Only a real (numeric) PR has a comment thread. Ref-/UI-targeted runs have
    # no PR — and the engine derives `pr` from the instance there (e.g. "ui-e2e3"),
    # which is non-empty — so guard on isdigit, not emptiness. Status for ref runs is
    # served by the visibility API. (The gh call below uses check=True and would
    # raise on repos/<r>/issues/<non-numeric>/comments.)
    if not str(pr).isdigit():
        return

    state = load_yaml(sf)
    cid = state.get("status_comment_id", "") or ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not cid:
        result = run_gh_rotating(
            [f"repos/{repo}/issues/{pr}/comments", "-f", f"body={body}", "--jq", ".id"],
            check=True)
        new_cid = result.stdout.strip()
        state["status_comment_id"] = int(new_cid) if new_cid.isdigit() else new_cid
        dump_yaml(sf, state)
    else:
        run_gh_rotating(
            ["-X", "PATCH", f"repos/{repo}/issues/comments/{cid}", "-f", f"body={body}"],
            check=True)


def post_pr_comment(pr, body):
    """
    post_pr_comment <pr> <body>
    Post a NEW (untracked) PR/issue comment — used for one-off engine notices
    (e.g. HITL override announcements and refusals). Unlike upsert_status_comment
    it does not track or edit an id. Best-effort; ENGINE_LOCAL short-circuits.
    """
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] pr comment pr#{pr}: {body}\n")
        return
    if not str(pr).isdigit():   # ref-/UI-targeted run: no real PR thread
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    result = run_gh_rotating([f"repos/{repo}/issues/{pr}/comments", "-f", f"body={body}"])
    if result.returncode != 0:
        sys.stderr.write(f"[engine] pr comment post failed (needs issues:write): {result.stderr.strip()}\n")


def create_issue(title, body):
    """Open a GitHub issue; return its number as a string (or "" on failure).
    Used by interactive (no-PR) question human tasks. Best-effort; ENGINE_LOCAL → log +
    return a stub number so the human task still records an issue locally."""
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] create issue: {title}\n")
        return "0"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    r = run_gh_rotating(
        [f"repos/{repo}/issues", "-f", f"title={title}", "-f", f"body={body}", "--jq", ".number"])
    if r.returncode != 0:
        sys.stderr.write(f"[engine] create issue failed (needs issues:write): {r.stderr.strip()}\n")
        return ""
    return r.stdout.strip()


def close_issue(number, comment=""):
    """Comment (optional) then close an issue. Best-effort; ENGINE_LOCAL → log."""
    if not str(number).strip():
        return
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] close issue #{number}: {comment}\n")
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if comment:
        run_gh_rotating([f"repos/{repo}/issues/{number}/comments", "-f", f"body={comment}"])
    r = run_gh_rotating(["-X", "PATCH", f"repos/{repo}/issues/{number}", "-f", "state=closed"])
    if r.returncode != 0:
        sys.stderr.write(f"[engine] close issue failed (needs issues:write): {r.stderr.strip()}\n")


def finalize_superseded_comment(pr, cid, body):
    """One-time edit of an ABANDONED status comment on reset: PATCH the comment
    `cid` to `body` (a superseded banner prepended above its frozen final state),
    then never touch it again — the caller drops status_comment_id so the next
    run creates a fresh comment. Best-effort: a failure (e.g. the comment was
    deleted) is logged, not fatal, so it never aborts the reset. ENGINE_LOCAL
    short-circuits (and logs, so tests can assert the call)."""
    if not cid:
        return
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] supersede comment {cid} pr#{pr}: {body}\n")
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    result = run_gh_rotating(
        ["-X", "PATCH", f"repos/{repo}/issues/comments/{cid}", "-f", f"body={body}"])
    if result.returncode != 0:
        sys.stderr.write(f"[engine] supersede comment {cid} failed (non-fatal): {result.stderr.strip()}\n")


def render_fork_status_body(dir_, pid, instance, proto):
    """
    render_fork_status_body <state_dir> <pid> <instance> <protocol.json>
    Pure projection of ALL fan-out branch state files into ONE combined PR-comment body.
    """
    branch_val = os.environ.get("STATE_BRANCH", STATE_BRANCH)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    link = f"https://github.com/{repo}/tree/{branch_val}/{pid}/{instance}"

    # load_protocol, not load_yaml: leg_report_paths below assumes every leg is
    # a `sequence` (lib.normalize_protocol's contract) — a raw load would hand
    # it an un-wrapped agent/code leg.
    protocol = load_protocol(proto)

    # Find the fork state and its legs. Static: the declared branches[]. Dynamic
    # (expand present): synthesize one leg per persisted manifest entry so the human
    # status comment renders dynamic legs (check-run gating already uses the manifest).
    branches = []
    fo_id = None
    fork_node = {}
    for state in protocol.get("states", []):
        if state.get("kind") == "fork":
            fo_id = state.get("id")
            fork_node = state
            if state.get("expand"):
                each = state.get("each", {})
                man = read_manifest(dir_, pid, instance, [fo_id])
                for leg in man.get("legs", []):
                    branches.append({"id": leg["id"],
                                     "max_iterations": each.get("max_iterations", "?")})
            else:
                for b in state.get("branches", []):
                    branches.append(b)
            break

    sections = ""
    states_list = []

    for b in branches:
        bid = b["id"]
        # Resolve through the protocol, exactly as the multi-phase renderer does:
        # every leg is a `sequence` (lib.normalize_protocol), so its cursor,
        # history and output live in THREE different files.
        cur_p, hist_p, ev_p, max_iter = leg_report_paths(protocol, [fo_id], bid)
        if b.get("max_iterations") is not None:
            max_iter = b["max_iterations"]     # dynamic legs carry it on the synthesized entry
        _, lines = _render_leg_section(
            state_file(dir_, pid, instance, path=hist_p), max_iter)
        st, _ = _render_leg_section(
            state_file(dir_, pid, instance, path=cur_p), max_iter)
        vnote = _evidence_status_note(dir_, pid, instance, fo_id, bid,
                                      (fork_node.get("params") or {}).get("status_note"),
                                      ev_path=ev_p)

        states_list.append(st)
        sections += f"**{bid}**{vnote}\n\n{lines}\n\n"

    # Headline from branch states
    any_active = False
    any_failed = False
    for st in states_list:
        if st == "done":
            pass
        elif st == "failed":
            any_failed = True
        else:
            any_active = True

    if any_active:
        headline = "⏳ Review in progress…"
    elif any_failed:
        headline = "❌ Review incomplete — a branch could not complete; merge is gated."
    else:
        headline = "✅ Review complete — published."

    return f"\U0001f50d **{pid} · {instance}**\n\n{sections}{headline}\n\n[Full state & audit trail]({link})\n"


DEFAULT_MAX_DEPTH = 5


def effective_max_depth(proto):
    """Return the protocol's configured max_depth, or DEFAULT_MAX_DEPTH if unset."""
    v = proto.get("max_depth")
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else DEFAULT_MAX_DEPTH


def check_depth(proto):
    """Raise ValueError if the protocol's static tree depth exceeds the cap."""
    d = _paths.max_static_depth(proto)
    cap = effective_max_depth(proto)
    if d > cap:
        raise ValueError(f"protocol depth {d} exceeds max_depth {cap}")


# The kinds a fork LEG may declare. A leg is this engine's unit of parallel work
# — the thing that owns a state file and is arbitrated by a barrier.
#   join/choice  — nothing to converge or route between at leg scope.
#   approval/question — coherent but untested; a `sequence` wrapper expresses it.
#   fork         — a nested fork is only ever ENTERED via a sub-pipeline leg
#                  (_seed_child has no fork arm), so accepting it here would
#                  promise a capability the engine does not implement.
LEG_KINDS = ("agent", "code", "sequence")

# Keys a leg may not carry: it is terminal-by-construction — its successor is the
# join, and its outcome belongs to the join.
_LEG_FORBIDDEN = ("conclude", "next", "on_blocked")

_LEG_WRAP_HINT = {
    "approval": "wrap it in a `sequence` leg",
    "question": "wrap it in a `sequence` leg",
    "fork": "wrap it in a `sequence` leg — a nested fork is entered through one",
    "join": "a leg has nothing to converge; put the join after the fork",
    "choice": "a leg has nothing to route between; route inside a `sequence` leg",
}

LEG_WORK_NODE_ID = "step"


def normalize_protocol(proto):
    """Return `proto` with every bare `agent`/`code` fork leg wrapped in a sequence.

    A leg declares one of LEG_KINDS. Two of them — `agent` and `code` — describe
    a leg whose whole work is ONE node; `sequence` describes a leg with its own
    step list. Downstream, that difference forced a fork in every site that
    touches a leg: a flat leg's cursor, history and output all coincide in one
    file, a sequence leg's do not (`_seed_child`, `_fork_action`,
    `paths._is_sequence_node`, `leg_report_paths`).

    Normalizing at LOAD removes the second shape. The leg keeps its id — it is
    what names the leg's check-run and its cursor file — and becomes the
    sequence; the node that does the work becomes a child named
    LEG_WORK_NODE_ID.

    The child id is a CONSTANT, not `<leg-id>-step`: a dynamic fork's `each`
    template carries no `id` (its id comes from the expander's items at
    runtime), so there is no leg id to derive one from. Six shipped `each`
    templates are id-less.

    Authors keep writing `kind: "agent"`: this never touches protocol.json on
    disk. Pure (deep-copies, never mutates the argument) and idempotent, so
    double-normalizing is a no-op — which matters because `load_protocol` is
    not the only way a dict reaches here in tests.
    """
    out = copy.deepcopy(proto)
    for state in out.get("states") or []:
        _normalize_node(state)
    return out


def _normalize_node(node):
    """Wrap the legs of `node` if it is a fork, then recurse. In-place; callers
    hold a copy."""
    if not isinstance(node, dict):
        return
    if node.get("kind") == "fork":
        legs = list(node.get("branches") or [])
        if isinstance(node.get("each"), dict):
            legs.append(node["each"])
        for leg in legs:
            _wrap_leg(leg)
            _normalize_node(leg)      # a nested fork now lives inside a sequence leg
        return
    for child in node.get("states") or []:
        _normalize_node(child)


def _wrap_leg(leg):
    """Rewrite a bare `agent`/`code` leg into a one-child sequence, in place.

    A `sequence` leg is already the target shape. Any other kind is illegal in
    leg position and is left alone for `_validate_leg` to reject with its own
    actionable message — normalizing an illegal leg would hide the error.
    """
    if not isinstance(leg, dict) or leg.get("kind") not in ("agent", "code"):
        return
    child = {k: v for k, v in leg.items() if k != "id"}
    child["id"] = LEG_WORK_NODE_ID
    for key in [k for k in leg if k != "id"]:
        del leg[key]
    leg["kind"] = "sequence"
    leg["states"] = [child]
    # NB: `leg` may legitimately have no "id" — a dynamic fork's `each` template
    # is id-less until the expander names it per item. Nothing here reads one.


def load_protocol(path, validate=False):
    """Parse a protocol.json and normalize it. THE seam — every engine entry
    point reads a protocol through this, so all of them agree on the tree shape
    (and therefore on every state-file path `lib.state_path` derives from it).

    `validate=True` runs `validate_protocol` over BOTH trees — the AUTHOR's
    first, then the normalized one — and raises ValueError on the first
    violation. Both are needed, and the order matters:

      * the AUTHOR's tree is the only one where `_validate_leg`'s rules can
        fire at all. Normalization erases the condition half of them test: a
        bare `agent`/`code` leg becomes a `sequence`, and `_wrap_leg` copies
        every key but `id` onto a synthetic `step` child, where `conclude`,
        `next`, `on_blocked` and a missing `script` are all perfectly legal.
        Validating only the normalized tree therefore ACCEPTED at runtime three
        shapes `protocol-lint.py` rejects — the same "lints clean but cannot
        run" split leg-kind existed to remove. Author-tree-first also means the
        message names the leg id the author wrote, not `step`.
      * the NORMALIZED tree is still validated because `_validate_leg` is
        deliberately narrower than the per-node rule body (no Rule 0/6/7 over a
        bare leg), so a wrapped `code` leg's `inputs`/`env` are only reached
        after the wrap.

    Opt-in rather than unconditional: `next.py` is the entry point that decides
    whether a run may START, and it is where an authoring error must be fatal.
    advance/join/run-checks act on a protocol a `start` already admitted.
    """
    with open(path) as fh:
        proto = json.load(fh)
    normalized = normalize_protocol(proto)
    if validate:
        validate_protocol(proto)        # the AUTHOR's tree — leg rules live here
        validate_protocol(normalized)   # the tree the engine actually runs
    return normalized


def _validate_leg(br, fork_id, path_hint):
    """Validate one fork leg (a `branches[]` entry or the `each` template).

    RUNS ON THE AUTHOR'S TREE. Half of these rules test a condition that
    `normalize_protocol` erases (a bare `agent`/`code` leg becomes a
    `sequence`), so they can only fire before the wrap. Both `protocol-lint.py`
    and `load_protocol(validate=True)` — which `next.py` uses — hand this the
    un-normalized dict for exactly that reason; see load_protocol. Runtime
    coverage is pinned by tests/engine/test_runtime_leg_validation.py, which
    drives next.py rather than calling validate_protocol on a raw dict.

    NARROWER than the per-node rule body `_validate_sequence` runs over a
    `sequence`'s `states[]` — deliberately, not by oversight. An `agent`/`code`
    leg only gets: kind-must-be-legal, the three forbidden terminal keys, and
    its own required-field check (`workflow` for `agent`; exactly one of
    `script`/`workflow` — the XOR half of Rule 12 — for `code`). It does NOT
    get:
      - Rule 0 (kind must be a known NODE_KINDS value) — kind is already
        checked against the strictly smaller LEG_KINDS above.
      - Rule 6 (`code` leg's `inputs[].from_fork` must name an in-scope fork)
        and Rule 7 (`code` leg's `env` must be a list of plain names) — a
        `code` leg is not walked through the `inputs`/`env` checks a `code`
        NODE gets.
      - The MODE-SPECIFIC half of Rule 12 (7.0.0): a dispatched `code` leg's
        `env` (forbidden — `conclude` is already covered unconditionally by
        `_LEG_FORBIDDEN` above, for every leg kind, not just dispatched code)
        and an inline `code` leg's `evidence`/`checks`/`params` (forbidden).
        Same reasoning as Rule 6/7: these are content-shape checks over keys
        that only mean something once the leg is walked as the per-node body
        walks a real node, not structural like the required-field XOR. They
        are NOT unchecked, though — `load_protocol(validate=True)` validates
        the NORMALIZED tree too (see below), where the leg has become a
        one-child sequence and its child (the wrapped `step`) IS walked by
        `_validate_sequence`'s full per-node body, Rule 12 included. A test in
        tests/engine/test_dispatched_code_validation.py pins that this second
        pass is what actually catches these two, not a leg-local duplicate.
    It DOES get its own copy of Rule 9 (a node with `states` must declare
    `kind: "sequence"`), below: an `agent`/`code` leg that also carries
    `states` used to be accepted, with the `states` silently unreachable. Now
    that `lib.normalize_protocol` wraps a bare leg into a one-child sequence,
    accepting it would be worse than unreachable — `_wrap_leg` copies every
    key but `id` onto the manufactured child, so the `states` block would
    land on the child too, reachable but meaningless (the child is a leaf
    node; nothing ever reads its `states`). Reject it instead.
    Why not unify the rest: those rules run inside `_validate_sequence`'s loop over a
    SEQUENCE's `sibling_ids`/`fork_ids` — the scope Rule 3/6's cross-references
    resolve against is the ENCLOSING sequence, not the leg's own fork's
    branches. A leg has no such sibling scope of its own to check against.
    Threading it through would mean either passing the enclosing sequence's
    `sibling_ids`/`fork_ids` down into every leg call (widening this
    function's signature and every caller) or extracting the per-node body
    into a second entry point — real surgery to `_validate_sequence`, not
    proportionate to close a gap that (a) is not a regression (leg validation
    was strictly weaker before this file's `_validate_leg` existed at all) and
    (b) no shipped protocol hits today. Tracked here, in the open, instead:
    the next person adding a rule to the per-node body should ask whether it
    also belongs here.

    ANSWERED for Rule 12 (7.0.0, the two-mode `code` node): the required-field
    XOR (`script`/`workflow`) DOES belong here, alongside `agent`'s `workflow`
    check, because it is structural and because a leg-level message can name
    the LEG id and the FORK id (`_validate_leg`'s house style) — the
    normalized-tree pass alone would report the wrapped child's constant id
    ('step'), which is why the per-node Rule 12 in `_validate_sequence` also
    carries a `reported_id` fallback to the enclosing leg id for exactly this
    case. The mode-specific exclusions (dispatched `env`, inline
    `evidence`/`checks`/`params`) do NOT belong here — see the bullet above;
    they are the same shape of content-shape check Rule 6/7 already declined
    to duplicate, for the same reason, and the normalized-tree pass already
    proves them reachable.
    """
    bid = br.get("id", "<unnamed>")
    kind = br.get("kind")
    if not kind:
        raise ValueError(
            f"leg '{bid}' of fork '{fork_id}' has no 'kind' — a leg declares what "
            f"it is; the engine no longer infers it from which keys are present. "
            f"Add \"kind\": \"agent\" (a single agent), \"code\" (a single "
            f"deterministic step), or \"sequence\" (a sub-pipeline)."
        )
    if kind not in LEG_KINDS:
        hint = _LEG_WRAP_HINT.get(kind, f"use one of: {', '.join(LEG_KINDS)}")
        raise ValueError(
            f"leg '{bid}' of fork '{fork_id}' declares kind='{kind}', which is not "
            f"valid in leg position — {hint}."
        )
    # Rule 9's own copy: a single-node leg has no step list of its own. Reject
    # BEFORE normalization would otherwise wrap it — `_wrap_leg` copies every
    # key but `id` onto the child it manufactures, so an unrejected `states`
    # here would land on the child, reachable but meaningless.
    if kind in ("agent", "code") and "states" in br:
        raise ValueError(
            f"leg '{bid}' of fork '{fork_id}' declares kind='{kind}' but also "
            f"carries 'states'. A single-node leg has no step list; declare "
            f"\"kind\": \"sequence\" if you meant a sub-pipeline."
        )
    for key in _LEG_FORBIDDEN:
        if key in br:
            raise ValueError(
                f"leg '{bid}' of fork '{fork_id}' declares '{key}', which a leg may "
                f"not carry: a leg is terminal by construction — its successor is "
                f"the join and its outcome belongs to the join. Move '{key}' onto a "
                f"node inside a `sequence` leg, or onto the node after the join."
            )
    # Rule 10 — `publish` is retired (5.0.0), same as the per-node rule in
    # `_validate_sequence`. A leg is not walked by that loop, so it needs its
    # own check — the same gap that let flat legs bypass validation before
    # leg-kind (Task 2) closed it.
    if "publish" in br:
        raise ValueError(
            f"leg '{bid}' of fork '{fork_id}' declares 'publish', which is retired — "
            f"work done by code is a `code` node. Replace it with a `code` node "
            f"carrying \"script\": \"{br['publish']}\" and an "
            f"\"inputs\": [{{\"from\": \"{bid}\", \"as\": \"evidence\"}}] entry; "
            f"put it INSIDE the leg as its terminal step to keep publishing eager."
        )
    # Rule 11 — `hook` is retired (5.0.0), same as the per-node rule in
    # `_validate_sequence`. A leg is not walked by that loop, so it needs its
    # own check — the same gap that let flat legs bypass validation before
    # leg-kind (Task 2) closed it.
    if kind == "code" and "hook" in br:
        raise ValueError(
            f"code leg '{bid}' of fork '{fork_id}' declares 'hook', which is "
            f"retired — the key is now 'script' (the task body, the peer of an "
            f"agent's 'workflow'). Rename it, and move the executable from "
            f"publish/ to scripts/."
        )
    if kind == "sequence":
        if not br.get("states"):
            raise ValueError(
                f"leg '{bid}' of fork '{fork_id}' is a sequence with no 'states'")
        _validate_sequence(br["states"], path_hint + [bid])
    elif kind == "agent" and not br.get("workflow"):
        raise ValueError(
            f"agent leg '{bid}' missing 'workflow' — add a \"workflow\": \"<name>\" "
            f"key to the '{bid}' leg")
    elif kind == "code":
        # Rule 12's own copy (7.0.0): a `code` leg is `script` (inline) XOR
        # `workflow` (dispatched), exactly like a `code` NODE — see the
        # per-node Rule 12 in `_validate_sequence` and the schema's code-arm
        # `_comment`. Kept here, not deferred to the normalized-tree pass
        # (unlike the mode-specific exclusions below), because the required-
        # field check is the one leg rule this function has always done
        # directly — it is what lets the message name the LEG id and the FORK
        # id the author wrote, before the wrap replaces both with a bare
        # 'step'/`sid`. See `_validate_leg`'s docstring for which OTHER
        # per-node rules stay deliberately out of this function.
        has_script = bool(br.get("script"))
        has_workflow = bool(br.get("workflow"))
        if has_script and has_workflow:
            raise ValueError(
                f"code leg '{bid}' of fork '{fork_id}' declares both 'script' "
                f"and 'workflow' — a code leg is EITHER inline (script: runs "
                f"in the plan job) OR dispatched (workflow: runs as its own "
                f"GitHub Actions workflow, in the same lane as an agent leg); "
                f"pick exactly one execution mode."
            )
        if not has_script and not has_workflow:
            raise ValueError(
                f"code leg '{bid}' of fork '{fork_id}' has neither 'script' "
                f"nor 'workflow' — add \"script\": \"<name>\" for an inline "
                f"(plan-job) leg, or \"workflow\": \"<name>\" for a dispatched "
                f"(agent-lane) leg."
            )


def _validate_sequence(states, path_hint):
    """Walk a list of state dicts (a sequence at `path_hint`) and raise ValueError
    with an actionable message + the offending node id for each authoring rule:

    Rule 1 — join.of unknown fork in scope
        A join's `of` must name a fork sibling in the SAME sequence.
        Rationale: join and its fork are always siblings at the same tree level
        (deep-fanout: join-analyze.of="analyze" are both in the "deep" sub-pipeline).

    Rule 2 — agent state missing workflow
        Every `kind:agent` state in THIS sequence must carry a `workflow` key.
        A fork's own branches are not members of `states` and are not walked
        by this rule — each leg declares its own `kind` (agent/code/sequence)
        and is checked separately by `_validate_leg`, which enforces the
        analogous requirement (an agent leg needs `workflow`, a code leg
        needs `script`, a sequence leg needs `states`).

    Rule 3 — question.questions_from nonexistent sibling
        A human task's `questions_from` (when set) must refer to another state id in
        the same enclosing sequence.

    Rule 4 — fork branches[] XOR expand+each (dynamic fan-out)
        A fork has exactly one of a static `branches[]` or a dynamic
        `expand`+`each` pair. `expand` must carry hook/as/id_from/max_legs
        (max_legs an int in [1,256]); `expand.matrix_fields`, when present, must
        be an array of non-empty strings (the subset of item keys inlined into
        matrix.leg.inputs — unset means the full item, see project_matrix_item).
        `each` is a leg like any `branches[]` entry — it declares a `kind`
        (agent/code/sequence) and is validated the same way, by `_validate_leg`.

    Rule 5 — join.policy must parse
        A join's optional `policy` must be accepted by `join_policy_satisfied`
        ('all', 'any', or 'quorum:<N|P%>').

    Rule 6 — merge input from_fork unknown fork in scope
        A merge input's `from_fork` (when present) must name a fork
        sibling in the SAME sequence, mirroring Rule 1.

    Rule 10 — `publish` is retired (5.0.0)
        A node may not declare `publish`. It was a named slot whose name the
        engine never checked: same trust zone, same output shape, same
        directory as a `code` node's body. Replace it with a `code` node.

    Rule 11 — `hook` is retired (5.0.0)
        A `code` node's task body key is `script`, not `hook` — `hook` implied
        an attachment; the body is schema-required, the peer of an agent's
        `workflow`. The executable also moved: `publish/` is now `scripts/`.

    Rule 14 — inputs[].from must not name a fork (use from_fork instead)
        A fork has many legs, not one evidence file; `from` naming one
        silently resolves to a phase reference nothing ever writes (a nulled
        input, no diagnostic). Any node's `inputs`, not just `code`'s.
    """
    # Collect ids and fork ids visible in this sequence for rule 1.
    sibling_ids = {s.get("id") for s in states if s.get("id")}
    fork_ids = {s.get("id") for s in states if s.get("kind") == "fork"}

    for st in states:
        sid = st.get("id", "<unnamed>")
        kind = st.get("kind", "")

        # Rule 0 — the kind must be one the engine implements.
        #
        # The JSON-Schema layer already rejects an unknown enum value, but it is
        # a DEV-ONLY dependency: protocol-lint.py "degrades to semantic-only"
        # without `jsonschema`, and the shipped runtime is Python 3 + PyYAML. So
        # on a client's machine THIS is the only layer that runs, and without
        # this rule a retired kind was accepted here — `start` then wrote state
        # to the branch and dispatched the first node, failing only later when
        # the bad node was finally entered. Reject up front, before any state
        # exists, which is what the fail-loud guard promises.
        #
        # A fork's `branches[]` are never members of THIS loop's `states` — a
        # leg is checked separately, by `_validate_leg` (which requires a
        # `kind` and rejects one that's missing it). This loop only walks the
        # states directly in the enclosing sequence, so only their DECLARED
        # kinds are checked here.
        if kind and kind not in _paths.NODE_KINDS:
            _retired = {"merge": "code", "fanout": "fork",
                        "gate": "approval' or 'question",
                        "deterministic": "code"}
            _hint = (f" — renamed to '{_retired[kind]}'" if kind in _retired
                     else f" — expected one of: {', '.join(sorted(_paths.NODE_KINDS))}")
            raise ValueError(f"node '{sid}' has unknown kind '{kind}'{_hint}")

        # Rule 10 — `publish` is retired (5.0.0). It was a named slot whose name
        # the engine never checked: same trust zone, same output shape, same
        # directory as a `code` node's body. Worse than a generic node, because
        # it implied a guarantee that did not exist.
        if "publish" in st:
            raise ValueError(
                f"node '{sid}' declares 'publish', which is retired — work done by "
                f"code is a `code` node. Replace it with a `code` node carrying "
                f"\"script\": \"{st['publish']}\" and an "
                f"\"inputs\": [{{\"from\": \"{sid}\", \"as\": \"evidence\"}}] entry; "
                f"put it INSIDE the leg as its terminal step to keep publishing eager."
            )

        # Rule 11 — `hook` is retired (5.0.0). A leg is not walked by this loop
        # (see `_validate_leg`'s own copy of this rule), so this only covers a
        # top-level (or nested-sequence) `code` node.
        if kind == "code" and "hook" in st:
            raise ValueError(
                f"code node '{sid}' declares 'hook', which is retired — the key is "
                f"now 'script' (the task body, the peer of an agent's 'workflow'). "
                f"Rename it, and move the executable from publish/ to scripts/."
            )

        # Rule 2a — top-level agent state missing workflow
        if kind == "agent" and not st.get("workflow"):
            # `load_protocol(validate=True)` walks BOTH trees, so this loop also
            # runs over the NORMALIZED one, where a bare agent/code LEG has been
            # wrapped into a one-child sequence whose child is the constant id
            # LEG_WORK_NODE_ID ("step") -- naming THAT id here tells an author
            # nothing when a protocol has several wrapped legs (they'd all read
            # "agent node 'step' missing 'workflow'"). Name the enclosing LEG
            # instead — the id the author actually wrote and can find — via the
            # last path_hint segment, exactly the id `_validate_leg`'s own
            # (pre-normalization) copy of this rule would have named.
            reported_id = (path_hint[-1] if sid == LEG_WORK_NODE_ID and path_hint
                          else sid)
            raise ValueError(
                f"agent node '{reported_id}' missing 'workflow' — add a "
                f"\"workflow\": \"<name>\" key to the '{reported_id}' state"
            )

        # Rule 13 — `dispatched-run` is a reserved check name (7.0.0). The
        # checks job in agentic-engine.yml synthesizes a verdict under this
        # name itself, failure-only, when a dispatched run's exit status was
        # nonzero (agent OR dispatched `code`) — see the "Run checks" step.
        # A protocol declaring a real check with the same name would collide
        # with that synthetic verdict in `.results`, silently duplicating or
        # shadowing it. Reject it wherever `checks[]` appears, not just on
        # `code` nodes — an `agent` node can declare it too.
        for c in st.get("checks", []) or []:
            if c.get("run") == "dispatched-run":
                raise ValueError(
                    f"node '{sid}' declares a check with run='dispatched-run' "
                    f"— that name is reserved: the engine itself synthesizes a "
                    f"failure-only 'dispatched-run' verdict when a dispatched "
                    f"run's job fails, so a protocol-authored check of the "
                    f"same name would collide with it. Rename the check."
                )

        # Rule 1 — join references unknown fork (+ policy validity)
        if kind == "join":
            of = st.get("of", "")
            if of and of not in fork_ids:
                raise ValueError(
                    f"join '{sid}' references unknown fork of='{of}' — "
                    f"make sure a fork with id='{of}' exists as a sibling of '{sid}'"
                )
            pol = st.get("policy")
            if pol is not None:
                try:
                    join_policy_satisfied(pol, 0, 0)  # parse-check only
                except ValueError:
                    raise ValueError(
                        f"join '{sid}' has invalid policy='{pol}' — use "
                        f"'all', 'any', or 'quorum:<N|P%>'"
                    )

        # Rule 3 — question.questions_from nonexistent sibling
        if kind == "question":
            qf = st.get("questions_from", "")
            if qf and qf not in sibling_ids:
                raise ValueError(
                    f"question '{sid}' has questions_from='{qf}' but no sibling state "
                    f"with id='{qf}' exists — add the source state or correct the name"
                )

        # Rule 12 — the two-mode `code` node (7.0.0): `script` (inline) XOR
        # `workflow` (dispatched) — see CLAUDE.md's ABI section and
        # tests/engine/test_dispatched_code_validation.py. The schema enforces
        # the XOR structurally; what it CANNOT express alongside
        # additionalProperties:false is "these keys are illegal only in the
        # OTHER mode" without duplicating the whole property map, so that half
        # lives here — see the schema's code-arm `_comment`.
        #
        # Also reached for a `code` LEG: `load_protocol(validate=True)` walks
        # the NORMALIZED tree too, where a bare `code` leg has been wrapped
        # into a one-child sequence whose child is the constant id
        # LEG_WORK_NODE_ID ("step") — same "several legs all read as 'step'"
        # problem Rule 2a documents for agent legs. `_validate_leg`'s own
        # (pre-normalization) copy of the required-field XOR fires first with
        # a message naming the real leg id + fork id, so this arm's own
        # messages only surface for a genuinely top-level/sequence-nested code
        # node OR for the exclusions `_validate_leg` deliberately does NOT
        # duplicate (env/evidence/checks/params — see its docstring); use the
        # same `reported_id` trick either way so those messages are legible.
        if kind == "code":
            reported_id = (path_hint[-1] if sid == LEG_WORK_NODE_ID and path_hint
                          else sid)
            has_script = bool(st.get("script"))
            has_workflow = bool(st.get("workflow"))
            if has_script and has_workflow:
                raise ValueError(
                    f"code node '{reported_id}' declares both 'script' and "
                    f"'workflow' — a code node is EITHER inline (script: runs in "
                    f"the plan job) OR dispatched (workflow: runs as its own "
                    f"GitHub Actions workflow); pick exactly one execution mode."
                )
            if not has_script and not has_workflow:
                raise ValueError(
                    f"code node '{reported_id}' has neither 'script' nor "
                    f"'workflow' — add \"script\": \"<name>\" for an inline "
                    f"(plan-job) step, or \"workflow\": \"<name>\" for a "
                    f"dispatched (agent-lane) step."
                )
            if has_workflow and st.get("conclude"):
                raise ValueError(
                    f"dispatched code node '{reported_id}' (workflow: set) "
                    f"declares 'conclude' — a dispatched job has a real exit "
                    f"code, and that exit code IS the node's verdict, so "
                    f"'conclude' has nothing to add. Put the evidence-derived "
                    f"verdict logic INSIDE the '{st.get('workflow')}' workflow "
                    f"itself and end it with 'exit 1' on failure; drop "
                    f"'conclude'."
                )
            if has_workflow and st.get("env"):
                raise ValueError(
                    f"dispatched code node '{reported_id}' (workflow: set) "
                    f"declares 'env' — a dispatched workflow declares its own "
                    f"secrets via its own 'permissions:'/'secrets:' blocks; "
                    f"'env' only forwards a secret to an INLINE subprocess, "
                    f"which does not exist here. Drop 'env' and add the secret "
                    f"to the '{st.get('workflow')}' workflow directly."
                )
            if has_script:
                dispatched_only = [k for k in ("evidence", "checks", "params")
                                    if k in st]
                if dispatched_only:
                    raise ValueError(
                        f"inline code node '{reported_id}' (script: set) "
                        f"declares {', '.join(repr(k) for k in dispatched_only)} "
                        f"— these belong to the DISPATCHED lane (workflow:), "
                        f"where a real job produces evidence a check can settle. "
                        f"An inline script's exit code is its own verdict; drop "
                        f"{'these keys' if len(dispatched_only) > 1 else 'this key'}"
                        f" or switch '{reported_id}' to 'workflow'."
                    )

        # Rule 14 — inputs[].from must not name a fork (use from_fork instead).
        # A fork has MANY legs, not one evidence file, so `from` naming a fork
        # id is never correct — but the schema's `input` definition allows any
        # string there, and resolve_inputs' 3-case resolver treats an id it
        # doesn't recognize as a PHASE reference: it builds a plausible-looking
        # path that nothing ever writes, so the consumer's evidence resolves to
        # {} at runtime with NO diagnostic anywhere (the first live failure this
        # bug class produced: `pack` declared `from: "scan"` where `scan` is a
        # fork, and got a silent null). Checked for every node's `inputs`, not
        # only `code` — an agent's `from` is exactly as wrong. Runs before
        # Rule 6 so a genuine `from_fork` typo (misspelled fork id) is still
        # caught there, not shadowed by this one.
        for inp in st.get("inputs", []) or []:
            frm = inp.get("from")
            if frm and frm in fork_ids:
                raise ValueError(
                    f"node '{sid}' input from='{frm}' names a fork, not a "
                    f"single state — a fork has many legs, so there is no one "
                    f"evidence file to read; use from_fork='{frm}' instead to "
                    f"collect all of its legs' evidence into inputs/<as>.json"
                )

        # Rule 6 — merge.from_fork must name a fork in scope
        if kind == "code":
            for inp in st.get("inputs", []) or []:
                ff = inp.get("from_fork")
                if ff and ff not in fork_ids:
                    raise ValueError(
                        f"code '{sid}' input from_fork='{ff}' names no fork in scope — "
                        f"make sure a fork with id='{ff}' exists as a sibling of '{sid}'"
                    )
            # Rule 7 — code.env must be a list of plain variable NAMES.
            # A hook gets a least-privilege env; `env` is the explicit opt-in for
            # the secrets it needs. Catching a malformed list here matters more
            # than usual: the failure mode otherwise is a hook silently missing
            # its token at runtime, on the state branch, in zone 4.
            envs = st.get("env")
            if envs is not None:
                if not isinstance(envs, list) or not all(
                        isinstance(e, str) and e and "=" not in e for e in envs):
                    raise ValueError(
                        f"code '{sid}' env must be a list of environment variable "
                        f"NAMES (e.g. [\"PUBLISH_TOKEN\"]), not values or assignments"
                    )

        # Rule A-2 — `on_blocked` with nothing able to produce a nonzero exit.
        #
        # `on_blocked: "halt"` declares what happens WHEN THIS NODE OBJECTS. An
        # agent objects via its `conclude` hook's exit code, or via a failed
        # block-severity check (which lib.block_exit folds in with no hook
        # involved). With neither, the key can never fire — and a dead key reads
        # as a guarantee that is not there.
        #
        # AGENT ONLY. A `code` node objects through its own hook's exit code,
        # with no checks and no conclude: code-review's `honesty-verdict` is
        # exactly that shape, and rejecting it would break a shipped protocol.
        # Same story for a DISPATCHED code node (workflow: set, 7.0.0) — its
        # exit code is its verdict, `conclude` is forbidden on it (Rule 12
        # above), so this rule staying agent-only is correct for that mode too.
        if kind == "agent" and st.get("on_blocked"):
            has_block = any(c.get("on_fail") == "block"
                            for c in (st.get("checks") or []))
            if not st.get("conclude") and not has_block:
                raise ValueError(
                    f"agent node '{sid}' declares on_blocked:\"{st['on_blocked']}\" but "
                    f"nothing can make it object: it has no 'conclude' hook and no "
                    f"on_fail:\"block\" check, so the key has no effect. Add a "
                    f"'conclude' hook, add a block-severity check, or drop on_blocked."
                )

        # Rule 9 — sequence (BPMN Embedded Subprocess): a group of steps
        # treated as one unit. Its children were previously UNVALIDATED (the
        # walker recursed only through forks), and a `states` list next to a
        # conflicting kind silently reported as a sequence with the declared
        # kind ignored.
        has_states = isinstance(st.get("states"), list)
        if has_states and kind not in ("", "sequence"):
            raise ValueError(
                f"node '{sid}' declares kind='{kind}' but also has 'states' — "
                f"a node with children is a sequence; drop the states or set "
                f"kind='sequence'"
            )
        if kind == "sequence" or (has_states and not kind):
            if not st.get("states"):
                raise ValueError(
                    f"sequence '{sid}' has an empty 'states' — a group with no "
                    f"children is a dead end"
                )
            _validate_sequence(st["states"], path_hint + [sid])

        # Rule 8 — choice (BPMN Exclusive Gateway) routing is fully resolvable
        # statically. A choice is the only node whose successor is chosen at
        # RUNTIME, so a bad target or an unresolvable `$.` path would otherwise
        # surface mid-run, on the state branch, after real agent work was spent.
        if kind == "choice":
            on = st.get("on") or {}
            if not on.get("from") or not on.get("path"):
                raise ValueError(
                    f"choice '{sid}' needs an 'on' with 'from' (the node whose "
                    f"evidence decides) and 'path' (a $.dotted field), e.g. "
                    f'"on": {{"from": "triage", "path": "$.verdict"}}'
                )
            if on["from"] not in sibling_ids:
                raise ValueError(
                    f"choice '{sid}' reads on.from='{on['from']}' but no sibling "
                    f"state with that id exists — it can never resolve"
                )
            if not str(on["path"]).startswith("$.") or "*" in str(on["path"]) \
                    or "[" in str(on["path"]) or ".." in str(on["path"]):
                raise ValueError(
                    f"choice '{sid}' on.path='{on['path']}' must be a simple "
                    f"$.dotted path (e.g. '$.verdict' or '$.a.b') — no wildcards "
                    f"or filters, same syntax as expand.id_from"
                )
            cases = st.get("cases")
            if not isinstance(cases, list) or not cases:
                raise ValueError(
                    f"choice '{sid}' needs a non-empty 'cases' array — a choice "
                    f"with no cases routes nowhere"
                )
            # `done`/`failed` are implicit terminals, valid targets everywhere.
            targets = sibling_ids | {"done", "failed"}
            seen_when = set()
            for c in cases:
                if not isinstance(c, dict) or "when" not in c or "next" not in c:
                    raise ValueError(
                        f"choice '{sid}' each case needs both 'when' (the value to "
                        f"match) and 'next' (where to go), got {c!r}"
                    )
                if c["when"] in seen_when:
                    raise ValueError(
                        f"choice '{sid}' has duplicate case when={c['when']!r} — "
                        f"two arms matching the same value make routing "
                        f"order-dependent, i.e. not exclusive"
                    )
                seen_when.add(c["when"])
                if c["next"] not in targets:
                    raise ValueError(
                        f"choice '{sid}' case when={c['when']!r} routes to "
                        f"next='{c['next']}', which names no sibling state "
                        f"(nor the terminals done/failed)"
                    )
            dflt = st.get("default")
            if dflt is not None and dflt not in targets:
                raise ValueError(
                    f"choice '{sid}' default='{dflt}' names no sibling state "
                    f"(nor the terminals done/failed)"
                )

        # Recurse into fork branches / validate dynamic expand+each
        if kind == "fork":
            has_static = bool(st.get("branches"))
            has_dynamic = bool(st.get("expand")) or bool(st.get("each"))
            if has_static == has_dynamic:
                raise ValueError(
                    f"fork '{sid}' must have exactly one of branches[] (static) "
                    f"or expand+each (dynamic) — not both, not neither"
                )
            if has_dynamic:
                exp = st.get("expand") or {}
                for req in ("hook", "as", "id_from", "max_legs"):
                    if not exp.get(req) and exp.get(req) != 0:
                        raise ValueError(
                            f"fork '{sid}' expand missing '{req}' — expand needs "
                            f"hook, as, id_from, and max_legs"
                        )
                ml = exp.get("max_legs")
                if not isinstance(ml, int) or isinstance(ml, bool) or not (1 <= ml <= 256):
                    raise ValueError(
                        f"fork '{sid}' expand.max_legs must be an int in [1,256], got {ml!r}"
                    )
                mf = exp.get("matrix_fields")
                if mf is not None and (not isinstance(mf, list) or not all(isinstance(x, str) and x for x in mf)):
                    raise ValueError(
                        f"fork '{sid}' expand.matrix_fields must be an array of non-empty strings"
                    )
                each = st.get("each") or {}
                _validate_leg(dict(each, id=each.get("id", "<each>")), sid, path_hint)
            else:
                for br in st.get("branches", []):
                    _validate_leg(br, sid, path_hint)


def validate_protocol(proto):
    """Validate a parsed protocol dict for common authoring errors.

    Raises ValueError with an actionable message naming the offending node id
    for each of the following high-value rules:
      - join.of references a fork not in scope (same sequence)
      - agent node (top-level or agent leg) missing 'workflow'
      - question.questions_from names a nonexistent sibling sub-state
      - merge input's from_fork references a fork not in scope (same sequence)
      - a fork leg (a branches[] entry or the each template) declares a
        'kind' of agent/code/sequence, matching its required field, and
        carries none of conclude/next/on_blocked (see _validate_leg)

    Intentionally does NOT validate: check file existence, schema references,
    trigger syntax, or anything that requires disk access — those belong in
    check/run-checks resolution, not here. Keep this rule set small (YAGNI).
    """
    _validate_sequence(proto.get("states", []), [])


def has_fork(protocol):
    """True iff the protocol has at least one fan-out state."""
    return any(s.get("kind") == "fork" for s in protocol.get("states", []))


def leg_report_paths(protocol, fork_path, leg_id):
    """Where the status comment reads a leg's CURSOR, HISTORY and OUTPUT.

    Returns (cursor_path, history_path, evidence_path, max_iterations) -- the
    three already through `state_path`, ready for `state_file` /
    `output_artifact_path`.

    Every leg is a `sequence` (lib.normalize_protocol wraps a bare agent/code leg
    at load), so a leg's cursor is its own file while its history and output
    live in its LAST child's — the three things the comment needs live in
    DIFFERENT files:

      - cursor   : the LEG node's own file, carrying the leg's `state`. This is
                   what says whether the leg is done, and it is deliberately NOT
                   the agent's: the agent finishes one step before the leg does,
                   so reporting the agent's state would call a leg done while
                   its publisher had not run.
      - history  : the iterate-with-feedback rounds, which belong to the first
                   AGENT sub-state. A `code` step has no `history`.
      - evidence : the leg's OUTPUT, which by definition is its LAST step's
                   output -- the same rule the engine's own `{"from": "<leg>"}`
                   resolution uses (`branch_output_substate`).

    `max_iterations` comes from whichever node actually declares it (the agent
    sub-state for a sequence leg), so "iteration 1/2" keeps its denominator.
    """
    leg_path = list(fork_path) + [leg_id]
    node = _paths.node_at_path(protocol, leg_path) or {}
    subs = node.get("states") or []
    cursor = state_path(protocol, leg_path)
    agent_sub = next((x for x in subs if x.get("kind") == "agent"), subs[0])
    return (cursor,
            state_path(protocol, leg_path + [agent_sub["id"]]),
            state_path(protocol, leg_path + [subs[-1]["id"]]),
            agent_sub.get("max_iterations", node.get("max_iterations", "?")))


def _render_leg_section(sf, max_iter):
    """Project one leg's state file into (state, checklist-lines).
    Mirrors the per-branch rendering in render_fork_status_body so the
    single-phase and multi-phase comments read identically per leg.
      missing file        → ("pending", "_pending_")
      file, empty history → (<state>, "_no iterations yet_")
      file, with history  → (<state>, "- ✅/✗ iteration n/m …")
    """
    if not os.path.isfile(sf):
        return "pending", "_pending_"
    data = load_yaml(sf)
    history = data.get("history", []) or []
    st = data.get("state", "") or ""
    if not history:
        return st, "_no iterations yet_"
    out = []
    for entry in history:
        it = entry.get("iteration", "?")
        fb = entry.get("feedback", "") or ""
        # `feedback` carries only iterate-severity failures, so a human task that fails
        # a block/advisory check leaves it empty. Fall back to the recorded checks
        # map so we never claim "all checks passed" when a non-iterate check failed.
        failed = [k for k, v in (entry.get("checks", {}) or {}).items() if v != "pass"]
        if fb:
            out.append(f"- ✗ iteration {it}/{max_iter} — {fb}")
        elif failed:
            out.append(f"- ⚠️ iteration {it}/{max_iter} — checks failed: {', '.join(sorted(failed))}")
        else:
            out.append(f"- ✅ iteration {it}/{max_iter} — all checks passed")
    return st, "\n".join(out)


def _evidence_status_note(d, pid, instance, ph_id, bid, cfg, ev_path=None):
    """Render a flagged note for a fan-out leg's status header from its evidence —
    driven ENTIRELY by the fork's `params.status_note` config so the generic engine
    carries no protocol vocabulary. The per-leg checklist reports 'all checks passed'
    from the FORM checks only, so a leg whose evidence carries a flag-worthy verdict /
    severity reads as clear without this. cfg keys (all optional):
      verdict_field + flag_verdicts[]   → flag when ev[verdict_field] ∈ flag_verdicts
      severity_field + flag_severities[]→ count findings[].<severity_field> ∈ flag_severities
      label (default "flagged"), emoji (default "⚠️").
    Returns '' when cfg is absent (callers pass it only for opted-in forks), the
    evidence is missing/malformed, or nothing matched.
    """
    if not isinstance(cfg, dict):
        return ""
    # `ev_path` is the leg's OUTPUT path, resolved by leg_report_paths (a
    # sequence leg's output is its LAST step's, not the leg node's own). The
    # (phase, branch) fallback is the legacy flat address, kept for callers
    # that have no protocol to resolve against.
    if ev_path is not None:
        path = output_artifact_path(d, pid, instance, path=ev_path, kind="evidence")
    else:
        path = output_artifact_path(d, pid, instance, branch=bid, phase=ph_id, kind="evidence")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path) as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        return ""
    if not isinstance(ev, dict):
        return ""
    vfield, flag_verdicts = cfg.get("verdict_field"), cfg.get("flag_verdicts") or []
    sfield, flag_sev = cfg.get("severity_field"), cfg.get("flag_severities") or []
    counts = {}
    if sfield and flag_sev:
        for f in (ev.get("findings") if isinstance(ev.get("findings"), list) else []):
            if isinstance(f, dict) and f.get(sfield) in flag_sev:
                counts[f[sfield]] = counts.get(f[sfield], 0) + 1
    verdict_flagged = bool(vfield and ev.get(vfield) in flag_verdicts)
    if not verdict_flagged and not counts:
        return ""
    parts = [f"{counts[s]} {s}" for s in flag_sev if counts.get(s)]
    detail = f" ({', '.join(parts)})" if parts else ""
    return f" — {cfg.get('emoji', '⚠️')} {cfg.get('label', 'flagged')}{detail}"


def render_pipeline_status_body(dir_, pid, instance, proto):
    """
    render_pipeline_status_body <state_dir> <pid> <instance> <protocol.json>
    Protocol-LEVEL projection for a MULTI-PHASE protocol: render every phase
    (agent + fan-out) in declared order into ONE PR-comment body. Unlike
    render_fork_status_body (single fan-out phase, <instance>/<branch>.yaml),
    this resolves each leg with its phase id, so fan-out legs are found at
    <instance>/<phase>.<branch>.yaml — the fix for PR #65's stuck "_pending_".
    The audit link points at the instance directory (all phases live under it).
    """
    branch_val = os.environ.get("STATE_BRANCH", STATE_BRANCH)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    link = f"https://github.com/{repo}/tree/{branch_val}/{pid}/{instance}"

    # load_protocol, not load_yaml: leg_report_paths below assumes every leg is
    # a `sequence` (lib.normalize_protocol's contract) — a raw load would hand
    # it an un-wrapped agent/code leg.
    protocol = load_protocol(proto)
    inf = instance_file(dir_, pid, instance)
    inst = load_yaml(inf) if os.path.isfile(inf) else {}
    overridden = {o.get("phase") for o in (inst.get("overrides") or [])}
    halted = inst.get("halted") or {}
    halted_phase = halted.get("phase") if halted.get("reason") == "blocked" else None

    sections = ""
    any_active = any_failed = False
    human_task_open = False
    blocked_phase = None

    for ph in pipeline_states(protocol):
        ph_id = ph["id"]
        if ph.get("kind") == "fork":
            for b in ph.get("branches", []):
                bid = b["id"]
                # Resolve through the protocol: every leg is a `sequence`, so
                # its state, history and evidence live in three different files.
                cur_p, hist_p, ev_p, max_iter = leg_report_paths(protocol, [ph_id], bid)
                _, lines = _render_leg_section(
                    state_file(dir_, pid, instance, path=hist_p), max_iter)
                st, _ = _render_leg_section(
                    state_file(dir_, pid, instance, path=cur_p), max_iter)
                vnote = _evidence_status_note(dir_, pid, instance, ph_id, bid,
                                              (ph.get("params") or {}).get("status_note"),
                                              ev_path=ev_p)
                sections += f"**{ph_id} · {bid}**{vnote}\n\n{lines}\n\n"
                if st == "done":
                    pass
                elif st == "failed":
                    any_failed = True
                else:  # pending / in-flight
                    any_active = True
        elif _paths.is_human_task(ph.get("kind")):
            sf = state_file(dir_, pid, instance, phase=ph_id)
            if not os.path.isfile(sf):
                continue  # human task not reached yet → no row (output unchanged)
            g = (load_yaml(sf).get("human_task") or {})
            gstate = g.get("state", "")
            hist = g.get("history") or []
            who = (hist[-1].get("actor") if hist else "") or ""
            if gstate == "approved":
                note = f"✅ approved by @{who}"
            elif gstate == "rejected":
                note = f"⛔ rejected by @{who}"
                any_failed = True
            elif gstate == "changes_requested":
                note = f"🔁 changes requested by @{who} — push a fix or `/approve`"
                human_task_open = True
            else:  # open
                note = "⏳ awaiting human sign-off (`/approve` · `/request-changes` · `/reject`)"
                human_task_open = True
            sections += f"**{ph_id}**\n\n{note}\n\n"
        else:  # agent phase
            max_iter = ph.get("max_iterations", "?")
            sf = state_file(dir_, pid, instance, phase=ph_id)
            st, lines = _render_leg_section(sf, max_iter)
            if ph_id == halted_phase:
                note = "\n⛔ blocked — a required check did not pass; a write-access user can `/override`."
                blocked_phase = ph_id
            elif ph_id in overridden:
                note = "\n⚠️ blocked → overridden; proceeding."
            elif st == "done":
                note = "\n✅ clear."
            elif st == "failed":
                note = "\n❌ failed."
                any_failed = True
            else:  # pending / in-flight
                note = ""
                if st != "done":
                    any_active = True
            sections += f"**{ph_id}**\n\n{lines}\n{note}\n\n"

    if blocked_phase:
        headline = (f"⛔ Blocked at **{blocked_phase}** — a write-access user can comment "
                    f"`/override <reason>` to proceed past it.")
    elif human_task_open:
        headline = ("⏳ Awaiting human approval — comment `/approve`, "
                    "`/request-changes`, or `/reject`.")
    elif any_failed:
        headline = "❌ Pipeline failed — a human task could not complete; merge is gated."
    elif any_active:
        headline = "⏳ In progress…"
    else:
        headline = "✅ Pipeline complete — published."

    return f"\U0001f50d **{pid} · {instance}**\n\n{sections}{headline}\n\n[Full state & audit trail]({link})\n"


def render_instance_status_body(dir_, pid, instance, proto_path):
    """Pick the right shared-comment renderer for an instance-keyed comment:
    multi-phase → the protocol-level pipeline renderer; single-phase fan-out →
    the legacy fan-out renderer (kept byte-identical)."""
    # load_protocol, not load_yaml: only `is_multiphase` reads this copy today
    # (root-level `kind`s), but every protocol parse in this module goes
    # through the ONE seam regardless of what the caller currently touches, so
    # a later change here can't silently reintroduce an un-normalized tree.
    protocol = load_protocol(proto_path)
    if is_multiphase(protocol):
        return render_pipeline_status_body(dir_, pid, instance, proto_path)
    return render_fork_status_body(dir_, pid, instance, proto_path)


def ensure_status_comment(state_dir, pid, instance, proto_path, pr):
    """
    ensure_status_comment <state_dir> <pid> <instance> <protocol.json> <pr>
    Create-once guard for the shared instance-level status comment.  Reads the
    instance file's status_comment_id; if empty → render + upsert + cas_push;
    if already set → no-op.  Now also fires for a multi-phase protocol whose
    FIRST phase is an agent (e.g. preflight), so the protocol-level comment +
    audit link appear the moment the pipeline starts. A single-agent protocol
    (no fan-out, not multi-phase) has no shared comment → no-op.
    """
    # load_protocol, not load_yaml: see render_instance_status_body above.
    protocol = load_protocol(proto_path)
    if not is_multiphase(protocol) and not has_fork(protocol):
        return  # single-agent path: status lives in the per-state file, no shared comment
    inf = instance_file(state_dir, pid, instance)
    inst_data = load_yaml(inf) if os.path.isfile(inf) else {}
    cid = inst_data.get("status_comment_id", "") or ""
    if cid:
        # Already created on a previous run — idempotent no-op.
        return
    body = render_instance_status_body(state_dir, pid, instance, proto_path)
    upsert_status_comment(inf, pr, body)
    cas_push(state_dir, f"{instance}: ensure shared status comment")


def _gh_dispatch(event_type, fields):
    """Fire a repository_dispatch. ENGINE_LOCAL → no-op (logs to stderr in gh-args
    format). Delegates to run_gh_rotating (token-pool failover on a 403/429); if
    rotation still can't land the dispatch (None or nonzero) it raises so the
    calling job (inside next.py) fails RED instead of stalling silently. State is
    CAS-pushed before every dispatch call site, so nothing is lost — recovery is
    re-firing the printed `gh api` command."""
    args = [f"repos/{os.environ.get('GITHUB_REPOSITORY', '')}/dispatches",
            "-f", f"event_type={event_type}"]
    for k, v in fields.items():
        args += ["-F", f"client_payload[{k}]={v}"]
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        sys.stderr.write(f"[ENGINE_LOCAL] gh api {' '.join(args)}\n")
        return
    result = run_gh_rotating(args)
    if result is None or result.returncode != 0:
        err = result.stderr if result is not None else "no result"
        replay = "gh api " + " ".join(args)
        msg = (f"[engine] repository_dispatch {event_type} failed after token rotation: "
               f"{err}; state already pushed — recover by re-firing: {replay}")
        sys.stderr.write(msg + "\n")
        raise RuntimeError(msg)


def dispatch_continue(pid, instance, branch=None, substate=None, phase="", path=None):
    """Dispatch a protocol-continue event to resume a sub-pipeline leg.
    `path` (dot-joined tree path) drives the recursive NODE_PATH continue guard
    for NESTED legs; when set it is sent alone. The legacy branch/substate/phase
    form (depth-<=3) is byte-identical."""
    if path:
        _gh_dispatch("protocol-continue", {"protocol": pid, "instance": instance, "path": path})
        return
    f = {"protocol": pid, "instance": instance, "branch": branch, "substate": substate}
    if phase:
        f["phase"] = phase
    _gh_dispatch("protocol-continue", f)


def fire_join_dispatch(pid, instance, fork_path=""):
    """Dispatch a protocol-join event (all legs done; trigger the join barrier).
    `fork_path` (dot-joined TREE path of the enclosing fork) is carried as
    client_payload[path] ONLY for a NESTED fork; the TOP fork stays path-less
    (byte-identical to the legacy behavior)."""
    f = {"protocol": pid, "instance": instance}
    if fork_path:
        f["path"] = fork_path
    _gh_dispatch("protocol-join", f)


def materialize_inputs(resolved, target_dir):
    """Copy each existing resolved input to <target_dir>/inputs/<as>.json.
    Returns [{as, staged_path}] for the ones that existed."""
    inputs_dir = os.path.join(str(target_dir), "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    manifest = []
    for r in resolved:
        if not os.path.isfile(r["path"]):
            continue
        dst = os.path.join(inputs_dir, f"{r['as']}.json")
        shutil.copyfile(r["path"], dst)
        manifest.append({"as": r["as"], "staged_path": dst})
    return manifest


def stage_item(dir_, pid, instance, file_path, as_, item):
    """Persist a dynamic leg's item beside its state file as
    <...>.<as>.item.json, so the dispatch/materialize step can surface it as
    inputs/<as>.json for the leg's agent. Keyed by the leg's file-naming path."""
    dst = output_artifact_path(dir_, pid, instance, path=file_path, kind=f"{as_}.item")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w") as f:
        json.dump(item, f)


def project_matrix_item(item, matrix_fields):
    """Subset a dynamic leg's item to the keys that ride the GHA matrix.
    matrix_fields None/unset -> the full item (backward-compatible). Absent keys
    are skipped. The FULL item always stays durable on the state branch (stage_item);
    this only trims what is inlined into matrix.leg.inputs."""
    if not matrix_fields:
        return item
    return {k: item[k] for k in matrix_fields if k in item}


# GHA strategy.matrix / $GITHUB_OUTPUT practical ceiling; keep well under 1 MB.
_MATRIX_BYTES_CAP = 900_000


def check_matrix_size(legs):
    """Fail loud if the serialized matrix legs would exceed the GHA output/matrix
    cap. A protocol author who forgot `matrix_fields` gets a clear error, never a
    silent truncation (same discipline as max_legs over-cap)."""
    n = len(json.dumps(legs))
    if n > _MATRIX_BYTES_CAP:
        raise ValueError(
            f"matrix legs serialize to {n} bytes (> {_MATRIX_BYTES_CAP}); "
            f"set the fork's expand.matrix_fields to inline only small keys "
            f"(large fields stay on the state branch; the agent re-fetches them)")


def run_code_hook(dir_, pid, instance, proto_path, code_state, consuming_path=None):
    """Resolve+materialize a merge state's inputs and run its trusted reduce hook.
    Returns {conclusion, summary}; neutral fallback on any resolution/exec error.

    `consuming_path` is the merge node's TREE path. For a NESTED merge (a per-file
    `reduce` inside a sub-pipeline leg — path length > 1), a `from_fork` resolves
    RELATIVE to that path: the fork is the merge's sibling in the same
    (sub-)sequence, i.e. `consuming_path[:-1] + [fork_id]`; plain `from` inputs
    resolve path-aware from the same scope. For the TOP merge (consuming_path None
    or length 1) resolution is byte-identical to the pre-nesting behavior: the
    fork is the top-level `[fork_id]` and plain inputs use the legacy 3-case
    resolver (consuming_path suppressed)."""
    pdir = os.path.dirname(os.path.abspath(proto_path))
    proto = load_protocol(proto_path)
    fo = _fork_state(proto)
    phase = fo["id"] if (fo and is_multiphase(proto)) else None
    code_inputs = code_state.get("inputs", [])
    # SCOPE, not leg-ness: a node below the root resolves `from`/`from_fork`
    # relative to its own enclosing sequence. Genuinely path-depth — it holds for
    # a fork leg AND for a node inside a top-level `sequence` group. (Contrast
    # next.py's code arm, where "nested" means "is a fork leg's terminal step"
    # and must ask enclosing_fork_path instead.)
    nested = bool(consuming_path) and len(consuming_path) > 1
    cp_for_inputs = consuming_path if nested else None
    # from_fork inputs have no `from` key — resolve_inputs only understands
    # `from`, so keep them out of that call and handle them in the loop below.
    plain_inputs = [inp for inp in code_inputs if "from" in inp]
    # Branch-id refs resolve against branch leg outputs (Plan 2 resolve_inputs).
    resolved = resolve_inputs(proto, dir_, pid, instance,
                              consuming_branch=None, consuming_phase=phase,
                              inputs=plain_inputs, consuming_path=cp_for_inputs)
    workdir = tempfile.mkdtemp(prefix="merge-")
    materialize_inputs(resolved, workdir)
    for inp in code_inputs:
        if inp.get("from_fork"):
            fo_id = inp["from_fork"]
            # Resolve the fork RELATIVE TO the merge's node-path: it is the
            # merge's sibling in the same (sub-)sequence → parent-of-merge + fork
            # id. Top merge → the top fork ([fo_id]).
            if nested:
                fo_tree_path = list(consuming_path[:-1]) + [fo_id]
            else:
                fo_tree_path = [fo_id]
            # A nested fork is NOT a top-level state, so state_by_id() would miss
            # it — address it by full tree path.
            fo_node = _paths.node_at_path(proto, fo_tree_path)
            if fo_node is None or not fork_is_materialized(dir_, pid, instance, fo_tree_path, fo_node):
                raise ValueError(
                    f"merge from_fork='{fo_id}': no manifest at {'.'.join(fo_tree_path)} "
                    f"(fork not materialized or misnamed)"
                )
            rows = collect_fork_evidence(dir_, pid, instance, fo_tree_path, fo_node, proto=proto)
            inputs_dir = os.path.join(workdir, "inputs")
            os.makedirs(inputs_dir, exist_ok=True)
            with open(os.path.join(inputs_dir, f"{inp['as']}.json"), "w") as f:
                json.dump(rows, f)
    res = resolve_executable(f"{pdir}/scripts", code_state.get("script", ""), pdir, "")
    kind, path = res.split("\t", 1)
    if kind == "ERR" or not os.access(path, os.X_OK):
        sys.stderr.write(f"[code] hook unresolved/not-exec: {path}\n")
        return {"conclusion": "neutral", "hook_failed": True,
                "summary": "code hook unresolved"}
    # The trusted hook posts its combined PR comment via lib.post_pr_comment, which
    # reads PR from the env. In the unified engine the merge runs from next.py in the
    # PLAN job, which does not set PR (pre-4a it ran in protocol-join.yml, which did),
    # so derive PR from the instance for the hook subprocess. setdefault keeps any
    # PR the job already provides. (Live-found: combine merge comment silently dropped.)
    # SECURITY: least privilege, mirroring run_expander. The plan job's env
    # carries PUBLISH_TOKEN / the dispatch PAT / an authenticated STATE_REMOTE;
    # a code hook that never asked for a token must not see them. A node opts in
    # explicitly with `"env": ["PUBLISH_TOKEN", ...]` — lintable and reviewable,
    # and it names WHICH secret, unlike a blanket `trusted: true`.
    hook_env = hook_base_env(instance)
    for name in (code_state.get("env") or []):
        if name in os.environ:
            hook_env[name] = os.environ[name]
    try:
        r = subprocess.run([path, workdir, instance], text=True, capture_output=True,
                           env=hook_env, timeout=hook_timeout_seconds())
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"[code] hook timed out after {hook_timeout_seconds()}s: {path}\n")
        return {"conclusion": "neutral", "hook_failed": True,
                "summary": f"code hook timed out after {hook_timeout_seconds()}s"}
    if r.returncode != 0:
        sys.stderr.write(f"[code] hook nonzero: {r.stderr}\n")
        # 4.0.0: nonzero is a VERDICT, not a crash. It colours the sequence red;
        # whether it also STOPS the run is declared on the node (`on_blocked`).
        return {"exit": r.returncode, "summary": _redact((r.stderr or "").strip()[-300:])}
    try:
        parsed = json.loads(r.stdout.strip())
        if isinstance(parsed, dict):
            parsed.setdefault("exit", 0)
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return {"conclusion": "neutral", "hook_failed": True, "exit": HOOK_FAILED_EXIT,
            "summary": "code hook returned unparseable output"}


def finalize_code_result(res):
    """Map a merge hook's result to the (conclusion, summary, label) the TOP-merge
    finalize arm publishes. This is only reached once the caller has already
    ruled out `hook_failed` (that halts before this is called), so an ABSENT
    `conclusion` here USUALLY means the hook ran, exited 0, and printed
    something other than a `conclusion` key -- the BPMN Script Task semantic
    where completion IS the signal: success. But `run_code_hook`'s nonzero-exit
    case ALSO returns no `conclusion` (it never got far enough to print one) --
    so an absent conclusion is only success when the exit code says the hook
    actually finished clean; a crashed TERMINAL merge (e.g. recover-mental-model's
    combine dying mid-push) must gate exactly like the pre-existing "no verdict
    at all" case below (failure + 'failed'), NOT report done/green with its
    traceback as the summary. An EXPLICIT verdict ('success' or 'failure', e.g.
    an honest / NOT-honest outcome) always passes through with the 'done' label
    (the pipeline completed; the verdict is the verdict), regardless of `exit`
    -- a deliberate conclusion wins over the exit code. A merge hook that RAN
    and deliberately reported `conclusion: 'neutral'` (e.g. "nothing to push")
    is handled upstream by the mid-pipeline halt guard, not here -- reaching
    this function with an explicit 'neutral' (or any other non-terminal value)
    still fails closed, since a TERMINAL merge has nowhere further to route it."""
    res = res if isinstance(res, dict) else {}
    concl = res.get("conclusion")
    summary = res.get("summary", "")
    if concl is None:
        # Absent conclusion: exit 0 (or no exit key at all) = ran clean = a
        # real success. A nonzero exit = crashed before producing any verdict
        # = falls through to the same "no verdict" gate as an explicit
        # non-terminal conclusion, below -- NOT the success/failure passthrough,
        # which is reserved for a hook that actually decided something.
        if res.get("exit"):
            return "failure", f"verdict hook failed — blocked: {summary or 'no verdict'}", "failed"
        return "success", summary, "done"
    if concl in ("success", "failure"):
        return concl, summary, "done"
    return "failure", f"verdict hook failed — blocked: {summary or 'no verdict'}", "failed"


def _cli(argv):
    if not argv:
        sys.stderr.write("lib.py: no subcommand given\n")
        sys.exit(2)
    cmd, args = argv[0], argv[1:]
    if cmd == "protocol-id":
        print(protocol_id(args[0]))
    elif cmd == "state-file":
        # state-file <dir> <pid> <instance> [branch] [phase]   (positional; pass "" for branch to get a phase-only path)
        print(state_file(*args))
    elif cmd == "instance-file":
        print(instance_file(*args))
    elif cmd == "set-check-run":
        # set-check-run <name> <sha> <status> <conclusion> <title> <summary>
        set_check_run(*args)
    elif cmd == "match-run-by-cid":
        # match-run-by-cid <runs-json> <cid>
        # args[0] = runs_json, args[1] = cid  (same order as the bash function)
        result = match_run_by_cid(args[0], args[1])
        if result:
            print(result)
    elif cmd == "render-fork-status-body":
        # render-fork-status-body <dir> <pid> <instance> <protocol.json>
        print(render_fork_status_body(*args), end="")
    elif cmd == "upsert-status-comment":
        # upsert-status-comment <state_file> <pr> <body>
        upsert_status_comment(*args)
    elif cmd == "post-pr-comment":
        # post-pr-comment <pr> <body>
        post_pr_comment(args[0], args[1])
    elif cmd == "log-token-pool":
        # log-token-pool   (diagnostic; reads the pool from env, prints to stderr)
        log_token_pool()
    elif cmd == "cas-push":
        # cas-push <dir> <message>
        cas_push(*args)
    elif cmd == "resolve-executable":
        # resolve-executable <sdir> <name> <pdir> [exec]
        ex = args[3] if len(args) > 3 else ""
        print(resolve_executable(args[0], args[1], args[2], ex))
    elif cmd == "state-checkout":
        state_checkout(args[0])
    elif cmd == "ensure-status-comment":
        # ensure-status-comment <state_dir> <pid> <instance> <protocol.json> <pr>
        ensure_status_comment(args[0], args[1], args[2], args[3], args[4])
    elif cmd == "match-trigger":
        # match-trigger <protocol.json> <event_name> <action> <comment_body> [is_pr_comment]
        # The 5th positional defaults to "true" (back-compat for 4-arg callers);
        # only "false" flips it (a comment on a plain issue, not a PR).
        proto = load_protocol(args[0])
        ev = args[1] if len(args) > 1 else ""
        act = args[2] if len(args) > 2 else ""
        body = args[3] if len(args) > 3 else ""
        ispr = args[4] if len(args) > 4 else "true"
        print(match_trigger(proto, ev, act, body, is_pr_comment=(ispr.lower() != "false")))
    elif cmd == "route":
        # route <protocols_dir> <event_name> <action> <comment_body> <dispatch_protocol> <is_pr_comment>
        pdir = args[0]
        ev = args[1] if len(args) > 1 else ""
        act = args[2] if len(args) > 2 else ""
        body = args[3] if len(args) > 3 else ""
        disp = args[4] if len(args) > 4 else ""
        ispr = (args[5].lower() == "true") if len(args) > 5 else True
        try:
            r = route(pdir, ev, act, body, disp, ispr)
        except ValueError as e:
            sys.stderr.write(f"lib.py route: {e}\n")
            sys.exit(1)
        print(f"protocol={r['protocol']}")
        print(f"skip={'true' if r['skip'] else 'false'}")
    else:
        sys.stderr.write(f"lib.py: unknown subcommand {cmd}\n")
        sys.exit(2)


if __name__ == "__main__":
    _cli(sys.argv[1:])

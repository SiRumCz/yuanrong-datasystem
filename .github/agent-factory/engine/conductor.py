"""Local conductor: walk a whole protocol end-to-end outside GitHub (mock mode).

The conductor is a DISPATCH ROUTER. In production advance.py/join.py fire
repository_dispatch events that GitHub routes to a fresh engine run; under
ENGINE_LOCAL those events are written to stderr. The conductor parses them and
routes the next step, running driver primitives in a loop. It reimplements event
routing only — never engine logic.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contextlib
import glob
import json
import re
import shutil
import subprocess
import tempfile

import yaml

import driver  # sibling engine module
import lib  # sibling engine module (read-only: path-naming helpers only)
import paths  # sibling engine module

_EVENT_RE = re.compile(r"event_type=(\S+)")
_FIELD_RE = re.compile(r"client_payload\[([^\]]+)\]=(\S+)")


def parse_dispatches(stderr):
    """Extract [{event_type, fields}] from an ENGINE_LOCAL stderr stream, in order."""
    out = []
    for line in stderr.splitlines():
        m = _EVENT_RE.search(line)
        if not m:
            continue
        fields = {k: v for k, v in _FIELD_RE.findall(line)}
        out.append({"event_type": m.group(1), "fields": fields})
    return out


class AgentProvider:
    """Supplies a node's evidence.json in mock mode. The conductor is agnostic to
    how — recorded traces, programmable fixtures, etc."""
    def provide(self, node_path, iteration):
        raise NotImplementedError


class ProgrammableProvider(AgentProvider):
    """Evidence supplied directly by a scenario. `by_path` maps a node path to
    either one evidence dict (same every iteration) or a list of per-iteration
    dicts (1-based). Unlisted paths get {} (empty evidence).

    `trajectory_by_path` optionally maps a node path to that node's trajectory
    (a list of normalized action records) — the offline analogue of the
    `agent-stdio.log` trajectory a live run captures. A path with no entry
    yields None (trajectory unavailable), which `claims-attested` treats as
    unverified rather than as an empty-but-present trajectory."""
    def __init__(self, by_path, trajectory_by_path=None):
        self.by_path = by_path
        self.trajectory_by_path = trajectory_by_path or {}

    def provide(self, node_path, iteration):
        spec = self.by_path.get(node_path)
        if spec is None:
            return {}
        if isinstance(spec, list):
            return spec[min(iteration - 1, len(spec) - 1)]
        return spec

    def trajectory(self, node_path):
        """Records for this node, or None if the scenario declared none."""
        return self.trajectory_by_path.get(node_path)


class RecordingProvider(AgentProvider):
    """Wraps an inner provider; writes each provided evidence to
    trace_dir/<key>__<iteration>.json (key = node_path with '.'->'_'). Replay it
    later with RecordedProvider(trace_dir)."""
    def __init__(self, inner, trace_dir):
        self.inner = inner
        self.trace_dir = trace_dir
        os.makedirs(trace_dir, exist_ok=True)

    def provide(self, node_path, iteration):
        evidence = self.inner.provide(node_path, iteration)
        key = (node_path or "root").replace(".", "_")
        with open(os.path.join(self.trace_dir, f"{key}__{iteration}.json"), "w") as f:
            json.dump(evidence, f)
        return evidence


def _fresh_dir(workdir, tag):
    d = os.path.join(workdir, f"cx-{tag}")
    return d  # driver's CLIs clone STATE_REMOTE into this (must not pre-exist)


@contextlib.contextmanager
def _scratch(workdir, tag):
    """A per-step state-branch clone dir, removed the instant its step consumes it.

    The driver CLI clones STATE_REMOTE into this dir, does its git work, and
    pushes results back to the bare origin; nothing reads the dir afterward (the
    next step re-clones fresh from the origin). So each is pure throwaway —
    leaving it behind leaks ~200 tmpfs inodes per step, ~84 per code-review walk,
    which accumulates across a full-suite run until /tmp's inode cap is hit and
    the concurrent walk flakes with ENOSPC. See
    tests/conductor/test_scratch_cleanup.py."""
    d = _fresh_dir(workdir, tag)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _read_instance(env, workdir, pid, instance):
    d = os.path.join(workdir, "cx-read")
    if os.path.exists(d):
        shutil.rmtree(d)
    subprocess.run(["git", "clone", "-q", "-b", "agentic-state", env["STATE_REMOTE"], d], check=True)
    path = os.path.join(d, pid, instance, "_instance.yaml")
    try:
        with open(path) as fh:
            return yaml.safe_load(fh)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _run_leg(proto, instance, provider, env, workdir, path, iteration, counter, trace=None):
    """provide → checks → advance for one agent leg; return emitted dispatches."""
    evidence = provider.provide(path, iteration)
    ev = os.path.join(workdir, f"ev-{counter}.json")
    with open(ev, "w") as fh:
        json.dump(evidence, fh)
    # Optional per-node trajectory (the offline analogue of the live
    # agent-stdio.log a claims-attested check attests claims against).
    # Providers without a `trajectory` method (RecordingProvider,
    # RecordedProvider, …) are unaffected — this stays inert unless the
    # provider actually declares one for this path.
    leg_env = env
    traj = getattr(provider, "trajectory", lambda _p: None)(path)
    if traj is not None:
        traj_file = os.path.join(workdir, f"traj-{counter}.json")
        with open(traj_file, "w") as fh:
            json.dump(traj, fh)
        leg_env = {**env, "TRAJECTORY_PATH": traj_file}
    verdicts = driver.run_checks(proto, path or "root", ev,
                                 os.path.join(workdir, "diff.txt"),
                                 os.path.join(workdir, "files.txt"),
                                 node_path=path, env=leg_env)
    if trace is not None:
        trace.append({"path": path, "iteration": iteration, "verdicts": verdicts,
                      "kind": "leg"})
    vp = os.path.join(workdir, f"v-{counter}.json")
    with open(vp, "w") as fh:
        json.dump(verdicts, fh)
    with _scratch(workdir, f"adv-{counter}") as adv:
        stderr = driver.advance(adv, instance, proto, vp, ev, node_path=path, env=env)
    return parse_dispatches(stderr)


def _resolve_open_gate(protocol_path, proto_dict, instance, gate_path,
                       gate_resolver, answer_resolver, env, workdir, counter):
    """Route an open gate at `gate_path` (a full dotted node path) to the answer
    flow (a data/question gate, `questions_from` set) or the approval flow
    (resolve_gate, unchanged); return the parsed follow-on dispatches."""
    gate_node = paths.node_at_path(proto_dict, gate_path.split(".")) or {}
    counter[0] += 1
    with _scratch(workdir, f"gate-{counter[0]}") as gate:
        if gate_node.get("questions_from"):
            # A data/question gate: next.py's `answer` command is path-aware on
            # its own (do_answer's _find_open_gate recursively scans the live
            # cursors) — no NODE_PATH needed, root or nested alike.
            stderr = driver.answer_gate(
                gate, instance, protocol_path,
                answer_resolver.answers(gate_path), env=env)
        else:
            # An approval gate: next.py's resolve-gate is ROOT-cursor-only today,
            # so this arm only resolves a top-level approval gate.
            stderr = driver.resolve_gate(
                gate, instance, protocol_path,
                gate_resolver.resolve(gate_path), env=env)
    return parse_dispatches(stderr)


def _detect_silently_opened_gate(proto_dict, workdir, env, pid, instance, leg_path, tag):
    """A sub-pipeline leg step whose next sibling is a gate is opened DIRECTLY by
    advance.py's nested-branch arm (lib.open_gate, no dispatch event — there is
    nothing further to auto-drive until a human/answer resolves it). The
    conductor's dispatch-router loop has nothing to route on in that case, so
    detect the side effect by reading state instead: if leg_path's parent-
    sequence cursor now points its sub_state at a gate whose gates.state is
    'open', return that gate's full dotted node path; else None. (Only a
    depth>=2 sub-pipeline leg can have a directly-opened gate this way — a
    root-level agent phase always dispatches a path-continue event.)"""
    segs = leg_path.split(".") if leg_path else []
    if len(segs) < 2:
        return None
    parent = segs[:-1]
    with _scratch(workdir, f"peek-{tag}") as d:
        subprocess.run(["git", "clone", "-q", "-b", "agentic-state", env["STATE_REMOTE"], d],
                       check=True)
        cf = lib.state_file(d, pid, instance, path=lib.state_path(proto_dict, parent))
        if not os.path.isfile(cf):
            return None
        with open(cf) as fh:
            cur = yaml.safe_load(fh) or {}
        sub = cur.get("sub_state", "")
        if not sub:
            return None
        gate_path = parent + [sub]
        if paths.node_kind(proto_dict, gate_path) != "gate":
            return None
        gf = lib.state_file(d, pid, instance, path=lib.state_path(proto_dict, gate_path))
        if not os.path.isfile(gf):
            return None
        with open(gf) as fh:
            gdata = yaml.safe_load(fh) or {}
        if gdata.get("gates", {}).get("state") == "open":
            return ".".join(gate_path)
        return None


def _plan_capturing(state_dir, instance, protocol_path, command, head_sha, node_path, env):
    """Run next.py once, returning (action_dict, follow_on_dispatches).

    driver.plan returns only the parsed stdout action. But a MERGE node planned
    with `continue` fires its follow-on dispatch (a NESTED merge → protocol-join
    for the enclosing fanout; a TOP merge with `.next` → protocol-continue) via
    _gh_dispatch, which under ENGINE_LOCAL goes to STDERR — exactly like advance.py
    and join.py. The conductor already routes advance/join stderr; this captures
    the same stream for a plan step so a merge reached by `continue` doesn't stall
    the walk. next.py must not be re-invoked (its hook already CAS-pushed), so both
    streams come from this single run. Mirrors driver.plan's argv/env."""
    argv = [str(driver.ENGINE_DIR / "next.py"), state_dir, instance, protocol_path,
            command, head_sha]
    merged = {**os.environ, **(env or {}), "NODE_PATH": node_path}
    r = subprocess.run(argv, capture_output=True, text=True, env=merged)
    if r.returncode != 0:
        raise driver.DriverError(f"next.py exited {r.returncode}: {r.stderr.strip()}")
    try:
        action = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise driver.DriverError(f"plan (capturing) emitted non-JSON stdout: {e}\n{r.stdout}") from e
    return action, parse_dispatches(r.stderr)


def run(protocol_path, instance, provider, env, head_sha="sha",
        command="start", pid=None, workdir=None, max_steps=200, gate_resolver=None,
        diff="", changed_files=None, answer_resolver=None, return_trace=False,
        record_dir=None):
    """Walk a protocol to terminal in mock mode; return the final _instance.yaml dict.

    diff/changed_files feed the crafted-diff seam: written once to
    workdir/diff.txt and workdir/files.txt (newline-joined), which _run_leg
    passes to driver.run_checks for every leg. Back-compat: an empty diff and
    no changed_files reproduces today's empty-diff/empty-files behavior.

    return_trace: when True, return (instance_dict, trace) where trace is a
    list of {"path", "iteration", "verdicts", "kind"} — one entry per leg
    execution, appended in the order legs run, so a crafting loop can see
    exactly which node/iteration a traversal diverged at. Back-compat: when
    False (default), return just the instance dict.

    record_dir: the `--record` capture (decision 1.5). When set, each provided
    evidence is captured under `<record_dir>/<protocol>/<instance>/` (a
    RecordingProvider wrap of the given provider), so a smoke (or mock) walk
    lays down golden traces a later RecordedProvider replays. Works with any
    provider — SmokeProvider records real-agent evidence; a mock provider pins
    a regression fixture.
    """
    with open(protocol_path) as fh:
        proto_dict = json.load(fh)
    if pid is None:
        pid = proto_dict["name"]
    if workdir is None:
        raise ValueError("workdir is required (a scratch dir for checkouts/evidence)")
    if record_dir is not None:
        provider = RecordingProvider(provider, os.path.join(record_dir, pid, instance))
    if gate_resolver is None:
        gate_resolver = ApproveGateResolver()
    if answer_resolver is None:
        answer_resolver = ProgrammableAnswerResolver({})
    with open(os.path.join(workdir, "diff.txt"), "w") as fh:
        fh.write(diff)
    with open(os.path.join(workdir, "files.txt"), "w") as fh:
        fh.write("\n".join(changed_files or []))

    # Track per-path iteration counts (a re-dispatched leg increments).
    iters = {}
    counter = [0]
    trace = []
    # Work queue items: ("plan", command, path) | ("join", path)
    queue = [("plan", command, "")]
    steps = 0
    while queue:
        steps += 1
        if steps > max_steps:
            raise RuntimeError(f"conductor exceeded max_steps={max_steps} (loop?)")
        kind, *rest = queue.pop(0)
        if kind == "plan":
            cmd, path = rest
            counter[0] += 1
            with _scratch(workdir, f"plan-{counter[0]}") as pd:
                action, plan_dispatches = _plan_capturing(
                    pd, instance, protocol_path, cmd, head_sha, path, env)
            a = action.get("action")
            if a == "run-agent":
                legs = [{"path": action.get("path", ""), "workflow": action.get("workflow")}]
            elif a == "run-fanout":
                legs = action.get("legs", [])
            elif a == "noop" and action.get("reason", "").startswith("gate-open:"):
                # gate_path is the full node path the conductor planned with (a
                # root-level gate's reason carries just its id, which IS the
                # whole path at depth 1; a nested gate's reason carries the full
                # dotted NODE_PATH — see next.py's gate-open emit sites).
                gate_path = action["reason"].split(":", 1)[1]
                queue += [(d["event_type"], d["fields"].get("path", ""))
                          for d in _resolve_open_gate(
                              protocol_path, proto_dict, instance, gate_path,
                              gate_resolver, answer_resolver, env, workdir, counter)]
                continue
            else:
                # terminal/halt/no-op for the leg itself — but a MERGE node
                # planned with `continue` emits its follow-on dispatch here (a
                # nested merge's protocol-join, a top merge's protocol-continue).
                # Route them so the walk doesn't stall past a merge.
                queue += [(d["event_type"], d["fields"].get("path", ""))
                          for d in plan_dispatches]
                continue
            for leg in legs:
                lp = leg.get("path", "")
                iters[lp] = iters.get(lp, 0) + 1
                counter[0] += 1
                dispatches = _run_leg(protocol_path, instance, provider, env, workdir,
                                      lp, iters[lp], counter[0], trace=trace)
                if not dispatches:
                    # A sub-pipeline leg step that lands directly on a gate is
                    # opened in-place by advance.py with no dispatch event — see
                    # _detect_silently_opened_gate. Resolve it here so the walk
                    # keeps going instead of silently stalling.
                    counter[0] += 1
                    gp = _detect_silently_opened_gate(
                        proto_dict, workdir, env, pid, instance, lp, counter[0])
                    if gp:
                        dispatches = _resolve_open_gate(
                            protocol_path, proto_dict, instance, gp,
                            gate_resolver, answer_resolver, env, workdir, counter)
                queue += [(d["event_type"], d["fields"].get("path", ""))
                          for d in dispatches]
        elif kind == "protocol-continue":
            (path,) = rest
            queue.append(("plan", "continue", path))
        elif kind == "protocol-join":
            (path,) = rest
            counter[0] += 1
            with _scratch(workdir, f"join-{counter[0]}") as jd:
                stderr = driver.join(jd, instance, protocol_path, node_path=path, env=env)
            queue += [(d["event_type"], d["fields"].get("path", ""))
                      for d in parse_dispatches(stderr)]
        # unknown event types are ignored (no route)
    inst = _read_instance(env, workdir, pid, instance)
    if return_trace:
        return inst, trace
    return inst


class GateResolver:
    """Supplies a human gate's decision in mock mode, as resolve-gate env."""
    def resolve(self, gate_path):
        raise NotImplementedError


def _gate_env(decision):
    return {"GATE_DECISION": decision, "GATE_ACTOR": "reviewer",
            "GATE_REASON": "", "GATE_PR_AUTHOR": "author"}


class ApproveGateResolver(GateResolver):
    """Approves every gate (default). Actor != author so approve_excludes_author passes."""
    def resolve(self, gate_path):
        env = _gate_env("approve")
        env["GATE_REASON"] = "lgtm"
        return env


class ProgrammableGateResolver(GateResolver):
    """Per-gate decisions. by_path maps gate_path -> 'approve'|'reject'|'request-changes'."""
    def __init__(self, by_path, default="approve"):
        self.by_path = by_path
        self.default = default

    def resolve(self, gate_path):
        return _gate_env(self.by_path.get(gate_path, self.default))


class AnswerResolver:
    """Supplies a question/data gate's answer-comment body in mock mode, as an
    ANSWER_BODY string for driver.answer_gate."""
    def answers(self, gate_path):
        raise NotImplementedError


class ProgrammableAnswerResolver(AnswerResolver):
    """Per-gate answers. `by_gate` maps a gate path -> either a dict
    {question_id: value} (formatted here into `/answer <id>: <value>` lines) or
    a raw ANSWER_BODY string (passed through verbatim). A path with no entry
    answers nothing (an empty body — the gate stays open/partial)."""
    def __init__(self, by_gate):
        self.by_gate = by_gate or {}

    def answers(self, gate_path):
        spec = self.by_gate.get(gate_path, {})
        if isinstance(spec, str):
            return spec
        return "\n".join(f"/answer {qid}: {val}" for qid, val in spec.items())


class RecordedProvider(AgentProvider):
    """Replays evidence recorded by RecordingProvider from trace_dir. Missing exact
    iteration → falls back to the highest recorded iteration for that key (clamp);
    a key with no traces → {}."""
    def __init__(self, trace_dir):
        self.trace_dir = trace_dir

    def provide(self, node_path, iteration):
        key = (node_path or "root").replace(".", "_")
        exact = os.path.join(self.trace_dir, f"{key}__{iteration}.json")
        if os.path.exists(exact):
            with open(exact) as f:
                return json.load(f)
        matches = glob.glob(os.path.join(self.trace_dir, f"{key}__*.json"))
        if not matches:
            return {}
        matches.sort(key=lambda p: int(re.search(r"__(\d+)\.json$", p).group(1)))
        with open(matches[-1]) as f:
            return json.load(f)


# ── smoke mode (decision 1.5): real agent CLI in the manifest-pinned container ──

_METADATA_RE = re.compile(r"^#\s*gh-aw-metadata:\s*(\{.*\})\s*$", re.M)
_MANIFEST_RE = re.compile(r"^#\s*gh-aw-manifest:\s*(\{.*\})\s*$", re.M)
_CODEX_RE = re.compile(r"@openai/codex@([0-9A-Za-z.\-]+)")


def read_lock_pins(lock_path):
    """Read the pins gh-aw stamps into a compiled `*.lock.yml`, so a smoke run
    reproduces the exact agent GitHub would run. A `gh aw compile` bump changes
    only these pins — SmokeProvider reads them programmatically, no stub to fix.

    Returns {compiler_version, agent_id, agent_model, codex_version,
    pinned_image, secrets}. Raises DriverError if the lock lacks the gh-aw
    metadata/manifest comment lines (not a compiled lock)."""
    with open(lock_path) as fh:
        text = fh.read()
    md = _METADATA_RE.search(text)
    mf = _MANIFEST_RE.search(text)
    if not md or not mf:
        raise driver.DriverError(
            f"{lock_path}: not a gh-aw compiled lock (no gh-aw-metadata/manifest)")
    meta = json.loads(md.group(1))
    manifest = json.loads(mf.group(1))
    pinned_image = None
    for c in manifest.get("containers", []):
        # the runtime image the agent CLI executes in (node:lts-alpine), pinned
        # by digest; the firewall/mcp/squid images are prod-only and dropped.
        if c.get("pinned_image") and str(c.get("image", "")).startswith("node:"):
            pinned_image = c["pinned_image"]
            break
    cx = _CODEX_RE.search(text)
    return {
        "compiler_version": meta.get("compiler_version"),
        "agent_id": meta.get("agent_id"),
        "agent_model": meta.get("agent_model"),
        "codex_version": cx.group(1) if cx else None,
        "pinned_image": pinned_image,
        "secrets": manifest.get("secrets", []),
    }


def read_agent_prompt(md_path):
    """The compiled prompt is the agent `.md` body — its markdown minus the YAML
    frontmatter block. gh-aw compiles this same body into the lock; smoke feeds
    the source so a prompt edit needs no recompile to try locally."""
    with open(md_path) as fh:
        text = fh.read()
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    return text.lstrip("\n")


def _extract_evidence_json(stdout):
    """Pull the evidence.json object out of a CLI's raw stdout. Prefer a clean
    whole-body parse; else scan for the LAST balanced top-level `{...}` (the CLI
    may print log lines around it). Raises DriverError if none parses."""
    s = stdout.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    starts = [i for i, ch in enumerate(stdout) if ch == "{"]
    for start in reversed(starts):
        depth = 0
        for j in range(start, len(stdout)):
            if stdout[j] == "{":
                depth += 1
            elif stdout[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(stdout[start:j + 1])
                    except json.JSONDecodeError:
                        break
    raise driver.DriverError("smoke agent stdout carried no parseable evidence JSON")


class SmokeProvider(AgentProvider):
    """Real-agent evidence (decision 1.5): runs the manifest-pinned agent CLI in
    its pinned container against the LOCAL diff, capturing raw output → evidence.

    For a node it resolves `workflow` from the protocol, reads the lock's pins
    (`read_lock_pins`) + the compiled prompt (`read_agent_prompt`), `docker run`s
    the pinned node image, installs the pinned `@openai/codex@X`, feeds it the
    prompt + the local diff, forwards LLM creds by NAME (never in argv), and
    extracts the evidence JSON from stdout.

    Deliberately DROPS, per spec — these are prod Actions wiring, not agent
    behavior, and are covered instead by one live run per lock change:
      * the squid egress firewall (prod egress-security),
      * the GitHub MCP server (smoke feeds the diff; the agent uses file access),
      * safe-outputs → issue creation (the publish boundary — the shim's job).

    Injectable seams keep it testable offline: `docker_bin` (a fake `docker` on
    PATH) and `runner(argv) -> stdout` (bypass docker entirely). `_docker_argv`
    is pure, so argv construction is unit-tested without any container.
    """
    def __init__(self, protocol_path, repo_root=".", workflows_dir=None,
                 diff="", changed_files=None, docker_bin="docker",
                 cred_env=("OPENAI_API_KEY", "CODEX_API_KEY"), runner=None):
        with open(protocol_path) as fh:
            self.proto = json.load(fh)
        self.repo_root = os.path.abspath(repo_root)
        self.workflows_dir = os.path.abspath(
            workflows_dir or os.path.join(self.repo_root, ".github", "workflows"))
        self.diff = diff
        self.changed_files = changed_files or []
        self.docker_bin = docker_bin
        self.cred_env = tuple(cred_env)
        self.runner = runner

    def _workflow_for(self, node_path):
        node = paths.node_at_path(self.proto, node_path.split(".")) if node_path else None
        wf = (node or {}).get("workflow")
        if not wf:
            raise driver.DriverError(
                f"smoke: node '{node_path or 'root'}' declares no agent workflow")
        return wf

    def _lock_path(self, workflow):
        return os.path.join(self.workflows_dir, f"{workflow}.lock.yml")

    def _md_path(self, workflow):
        return os.path.join(self.workflows_dir, f"{workflow}.md")

    def _docker_argv(self, pins, prompt_path):
        """Pure: the `docker run …` argv for the pinned CLI over the local repo.
        Creds are forwarded by NAME (`-e NAME`, value from the host env), never
        interpolated as values — mirrors the router's env:-not-run: rule."""
        if not pins.get("pinned_image"):
            raise driver.DriverError("smoke: lock has no pinned node image")
        if not pins.get("codex_version"):
            raise driver.DriverError("smoke: lock has no pinned @openai/codex version")
        argv = [self.docker_bin, "run", "--rm"]
        for name in self.cred_env:
            argv += ["-e", name]           # value comes from the host env, unread here
        argv += [
            "-v", f"{self.repo_root}:/workspace:ro",
            "-v", f"{prompt_path}:/smoke/prompt.txt:ro",
            "-w", "/workspace",
            pins["pinned_image"],
            "sh", "-c",
            f"npm install --ignore-scripts -g @openai/codex@{pins['codex_version']} "
            f">/dev/null 2>&1 && codex exec --skip-git-repo-check - < /smoke/prompt.txt",
        ]
        return argv

    def _compose_prompt(self, prompt):
        diff = self.diff or "(no diff supplied)"
        files = "\n".join(self.changed_files) or "(none)"
        return (f"{prompt}\n\n"
                f"## Changed files\n\n{files}\n\n"
                f"## Diff under review\n\n```diff\n{diff}\n```\n")

    def provide(self, node_path, iteration):
        workflow = self._workflow_for(node_path)
        pins = read_lock_pins(self._lock_path(workflow))
        prompt = read_agent_prompt(self._md_path(workflow))
        composed = self._compose_prompt(prompt)
        if self.runner is not None:
            # bypass docker entirely (test seam / alternate container runner)
            return _extract_evidence_json(self.runner(node_path, iteration, pins, composed))
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as pf:
            pf.write(composed)
            prompt_path = pf.name
        try:
            argv = self._docker_argv(pins, prompt_path)
            r = subprocess.run(argv, capture_output=True, text=True)
            if r.returncode != 0:
                raise driver.DriverError(
                    f"smoke agent ({workflow}) exited {r.returncode}: {r.stderr.strip()}")
            return _extract_evidence_json(r.stdout)
        finally:
            os.unlink(prompt_path)


def _capture_local_diff(base):
    """`git diff <base>...HEAD` for a smoke walk: the local change the agents
    review. Returns (diff_text, changed_files). base='' → empty (agents see the
    prompt only)."""
    if not base:
        return "", []
    diff = subprocess.run(["git", "diff", f"{base}...HEAD"],
                          capture_output=True, text=True).stdout
    names = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                           capture_output=True, text=True).stdout.split()
    return diff, names


def main(argv=None):
    """Thin CLI: walk a protocol locally in mock or smoke mode.

    Mock (default) replays/programs evidence — for a fixture, pass --evidence
    (a JSON {node_path: evidence} map). Smoke (--smoke) runs the real pinned
    agent CLI per leg via SmokeProvider over the local `git diff`. --record DIR
    captures each leg's evidence into golden traces. STATE_REMOTE (a bare
    agentic-state repo) and the usual ENGINE_LOCAL scaffolding come from the env,
    exactly like the driver primitives."""
    import argparse
    ap = argparse.ArgumentParser(prog="conductor", description=main.__doc__)
    ap.add_argument("protocol", help="path to protocol.json")
    ap.add_argument("instance", help="instance key, e.g. pr-1")
    ap.add_argument("--smoke", action="store_true",
                    help="real agents in the pinned container (needs docker + LLM creds)")
    ap.add_argument("--record", metavar="DIR", default=None,
                    help="capture each leg's evidence into DIR/<protocol>/<instance>/")
    ap.add_argument("--diff-base", default="",
                    help="smoke: git ref to diff HEAD against (the change agents review)")
    ap.add_argument("--evidence", default=None,
                    help="mock: JSON file mapping node_path -> evidence dict")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--command", default="start")
    ap.add_argument("--max-steps", type=int, default=400)
    args = ap.parse_args(argv)

    workdir = tempfile.mkdtemp(prefix="conductor-")
    env = dict(os.environ)
    env.setdefault("ENGINE_LOCAL", "1")
    if not env.get("STATE_REMOTE"):
        ap.error("STATE_REMOTE must point at a bare agentic-state repo")
    diff, changed = ("", [])
    if args.smoke:
        diff, changed = _capture_local_diff(args.diff_base)
        provider = SmokeProvider(args.protocol, repo_root=args.repo_root,
                                 diff=diff, changed_files=changed)
    else:
        by_path = json.load(open(args.evidence)) if args.evidence else {}
        provider = ProgrammableProvider(by_path)
    inst = run(args.protocol, args.instance, provider, env=env, workdir=workdir,
               command=args.command, diff=diff, changed_files=changed,
               max_steps=args.max_steps, record_dir=args.record)
    print(json.dumps({"terminal": inst.get("phase_label", ""), "instance": args.instance}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

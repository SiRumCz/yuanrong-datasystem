#!/usr/bin/env python3
"""hook.py — real-time Cedar authorization of agent tool calls (`PreToolUse`).

Phase B1 of the live-guard design
(`docs/superpowers/specs/2026-08-10-realtime-cedar-tool-call-guard-design.md`):
the successor to `.claude/hooks/pretooluse-probe.py`. Same ABI and the same
fail-open discipline; the hard-coded substring rule is replaced by a real Cedar
decision over the vendored universal policy corpus in `../policy/cedar/live`.

    stdin: PreToolUse event JSON
      -> normalize.normalize()        pure Python, no Node
      -> node decide.js               the ONE authorize seam (../_cedar-decide.js)
      -> stdout: {} | hookSpecificOutput.permissionDecision = "deny"

ABI (Claude Code PreToolUse): event JSON on stdin; ALWAYS exit 0. **Allow is the
empty object `{}`** — never an explicit `"allow"`, which would override the host's
own permission logic and silently widen what the agent may do. Deny is
`hookSpecificOutput.permissionDecision = "deny"` with a
`permissionDecisionReason` that names the Cedar policy which blocked the call; the
reason reaches the model verbatim, so a denial STEERS rather than merely failing.

FAILURE POSTURE — config, not a code edit (`CEDAR_LIVE_GUARD_FAILURE_MODE`):
  * `open` (default, development): any error — missing `node`, missing
    `@cedar-policy/cedar-wasm`, timeout, unparseable stdin, cedar evaluation error
    — yields `{}` and the tool proceeds. This hook governs the session developing
    it; a bug here must not wedge that session.
  * `closed` (production): the same errors DENY. Fail-closed covers "engine
    broken"; it cannot cover "hook never installed", which is why every session
    also gets a positive LIVENESS record (below).

EVIDENCE — three artifacts under `CEDAR_LIVE_GUARD_LOG_DIR`
(default `$TMPDIR/cedar-live-guard`):
  * `liveness/<session_id>.json` — the per-session POSITIVE liveness record the
    spec requires. Its ABSENCE means "unenforced"; it must never be read as
    "nothing to report". Rewritten atomically on every call with running counts.
  * `decisions.jsonl` — one line per call: the full event, the Cedar request, the
    decision, the determining policy ids, and the elapsed milliseconds. This is
    the evidence a later check inspects.
  * `incidents.jsonl` — one line per Cedar DENY only (never for an engine-outage
    deny — see `fail()`). Its ABSENCE means "no real policy violation this
    session"; a consumer reads this directly rather than filtering
    `decisions.jsonl` for denies. Each line carries `posture` — what the guard
    ASKED for. It deliberately does NOT record whether the agent stopped: this
    process cannot observe that. A consumer settles it from `decisions.jsonl`,
    which is append-ordered, so any decision after the last incident is a tool
    call the agent made in spite of being refused (see the `observed_outcome`
    helper in cedar-on-hook-test's `live-guard-enforced` check).

Environment:
  CEDAR_LIVE_GUARD_POLICY_DIR   policy set          (default ../policy/cedar/live)
  CEDAR_LIVE_GUARD_LOG_DIR      evidence dir        (default $TMPDIR/cedar-live-guard)
  CEDAR_LIVE_GUARD_FAILURE_MODE open | closed       (default open)
  CEDAR_LIVE_GUARD_TIMEOUT      seconds for decide.js (default 20)
  CEDAR_LIVE_GUARD_NODE         node executable     (default: `node` on PATH)
  CEDAR_LIVE_GUARD_DISABLE      truthy -> always `{}` (kill switch, still logged)
  CEDAR_LIVE_GUARD_ON_DENY      deny | steer-stop | stop   (default deny;
                                `stop` is CLAUDE ONLY -- see POSTURES below)
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize import normalize, normalize_all  # noqa: E402  (deliberate: sibling module)

HERE = Path(__file__).resolve().parent
HOOK_VERSION = "live-guard/1.0.0-b1"
DEFAULT_POLICY_DIR = HERE.parent / "policy" / "cedar" / "live"
DECIDE_JS = HERE / "decide.js"


def _env(name, default=""):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def log_dir() -> Path:
    return Path(_env("CEDAR_LIVE_GUARD_LOG_DIR",
                     str(Path(tempfile.gettempdir()) / "cedar-live-guard")))


def policy_dir() -> Path:
    return Path(_env("CEDAR_LIVE_GUARD_POLICY_DIR", str(DEFAULT_POLICY_DIR)))


def fail_closed() -> bool:
    return _env("CEDAR_LIVE_GUARD_FAILURE_MODE", "open").strip().lower() == "closed"


#: What the guard ASKS for after a Cedar deny. The call is blocked in every
#: posture — these differ only in what the agent is asked to do next.
#:
#:   deny        block, and steer the agent toward another approach.
#:   steer-stop  block, and instruct the agent to halt, carried in the REASON
#:               TEXT so no extra top-level key is emitted. Safe on both engines.
#:   stop        block, instruct the agent to halt, AND end the run via
#:               `continue: false`. CLAUDE ONLY -- those extra keys are
#:               unrecognized by codex, which then discards the whole decision
#:               and runs the command (docs/STATUS.md, "live-guard on codex").
#:
#: None of them can guarantee the agent stopped: `steer-stop` is persuasion, and
#: even `stop` is the host's to honour. Compliance is settled from the decision
#: log by a check, never asserted here.
POSTURES = ("deny", "steer-stop", "stop")


def on_deny() -> str:
    v = _env("CEDAR_LIVE_GUARD_ON_DENY", "deny").strip().lower()
    return v if v in POSTURES else "deny"


# --- evidence --------------------------------------------------------------

def record(entry: dict) -> None:
    """Append one line to the decision log. NEVER raises."""
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "decisions.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def record_incident(entry: dict) -> None:
    """Append one line to the incident log. NEVER raises.

    Separate from decisions.jsonl so a consumer reads denials directly rather
    than re-deriving them by filtering every call.
    """
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "incidents.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def touch_liveness(session_id: str, outcome: str, engine: str, event: dict) -> None:
    """Write/refresh the per-session POSITIVE liveness record. NEVER raises.

    A consumer that finds no record for a session must treat that session as
    UNENFORCED — an uninstalled hook is otherwise indistinguishable from a clean
    run.
    """
    try:
        d = log_dir() / "liveness"
        d.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (session_id or "unknown"))
        path = d / f"{safe}.json"
        now = time.time()
        # The counters are a read-modify-write, and a host may dispatch hooks for
        # overlapping tool calls: two processes that read the same value both
        # write value+1 and one increment is lost. `os.replace` makes each write
        # atomic but cannot make the SEQUENCE atomic. Observed live on PR 215
        # (run 31750438861): decisions.jsonl held 16 entries -- it is append-only,
        # so it loses nothing -- while counts read 4.
        #
        # A lock file beside the record serializes the whole sequence. It is a
        # separate file so the record itself is still replaced atomically rather
        # than written in place; holding the lock on the record would mean
        # writing through the fd and a reader could see a half file.
        lock_path = d / f"{safe}.lock"
        with lock_path.open("a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    rec = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(rec, dict):
                        raise ValueError
                except Exception:
                    rec = {
                        "schema": "cedar-live-guard/liveness/1",
                        "session_id": session_id,
                        "hook": HOOK_VERSION,
                        "hook_path": str(Path(__file__).resolve()),
                        "policy_dir": str(policy_dir()),
                        "failure_mode": "closed" if fail_closed() else "open",
                        "first_seen": now,
                        "counts": {},
                        "engines": {},
                    }
                rec["last_seen"] = now
                rec["cwd"] = event.get("cwd")
                rec["permission_mode"] = event.get("permission_mode")
                rec["counts"][outcome] = int(rec.get("counts", {}).get(outcome, 0)) + 1
                rec["engines"][engine] = int(rec.get("engines", {}).get(engine, 0)) + 1
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(rec, ensure_ascii=False, default=str),
                               encoding="utf-8")
                os.replace(tmp, path)  # atomic: a reader never sees a half file
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


# --- decision --------------------------------------------------------------

def run_decide(request: dict) -> dict:
    """Invoke decide.js. Returns the parsed decision, or raises on any failure."""
    node = _env("CEDAR_LIVE_GUARD_NODE") or shutil.which("node")
    if not node:
        raise RuntimeError("node executable not found")
    pdir = policy_dir()
    if not pdir.is_dir():
        raise RuntimeError(f"policy dir missing: {pdir}")
    if not DECIDE_JS.is_file():
        raise RuntimeError(f"decide.js missing: {DECIDE_JS}")
    try:
        timeout = float(_env("CEDAR_LIVE_GUARD_TIMEOUT", "20"))
    except ValueError:
        timeout = 20.0
    proc = subprocess.run(
        [node, str(DECIDE_JS), "-", str(pdir)],
        input=json.dumps(request), capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"decide.js exit {proc.returncode}: {proc.stderr.strip()[:400]}")
    out = json.loads(proc.stdout)
    if out.get("decision") not in ("Allow", "Deny"):
        raise RuntimeError(f"decide.js returned no decision: {proc.stdout.strip()[:200]}")
    return out


#: Appended when the posture asks the agent to halt. On codex this text is the
#: ONLY halt mechanism available, and it measurably works: with the `deny`
#: wording below the agent carried on in 3/3 trials, with this one in 0/3.
STOP_STEER = (" STOP IMMEDIATELY: this is a terminal security violation. Do not"
              " run any further tool calls, do not continue with remaining"
              " steps, and end your turn now reporting that the security guard"
              " stopped you.")


def deny_reason(out: dict, request: dict | None = None, posture: str = "deny") -> str:
    """The steer handed back to the model. Names the policy that blocked.

    When the call implied several requests (an `apply_patch` envelope), the
    offending PATH is named too: "denied" alone is unactionable for a patch
    touching a dozen files.

    The tail differs by posture: `deny` points the agent at another approach,
    while the halting postures tell it to stop instead — telling an agent to
    "choose a different approach" after a terminal violation is precisely the
    invitation to continue that the measurement above caught.
    """
    ids = [i for i in out.get("determining") or [] if isinstance(i, str)]
    descs = out.get("descriptions") or {}
    halting = posture in ("steer-stop", "stop")
    if not ids:
        # Deny with no determining forbid == no permit matched (Cedar is
        # default-deny). Say so precisely; it usually means a broken policy set.
        msg = ("Blocked by Cedar policy (live-guard): no policy permitted this "
               "call and Cedar denies by default. Do not retry this call; the "
               "policy set may be misconfigured.")
        return msg + STOP_STEER if halting else msg
    named = ", ".join(ids)
    detail = "; ".join(f"{i}: {descs[i].rstrip('.')}" for i in ids if isinstance(descs.get(i), str))
    msg = (f"Blocked by Cedar policy {named} (live-guard, universal policy set). "
           "This tool call is forbidden regardless of intent.")
    path = ((request or {}).get("context") or {}).get("path")
    if isinstance(path, str) and path:
        msg += f" Offending path: {path}."
    if detail:
        msg += f" Reason: {detail}."
    if halting:
        return msg + STOP_STEER
    return msg + (" Do not retry it or work around it — choose a different "
                  "approach, or ask the user.")


# --- ABI -------------------------------------------------------------------

def allow() -> None:
    print("{}")
    sys.exit(0)


def deny(reason: str, *, stop: bool = False) -> None:
    """Refuse one tool call.

    DO NOT add fields to the non-stop output. codex's PreToolUse output struct is
    strict: ANY unrecognized key makes it discard the whole decision, log
    `hook: PreToolUse Failed`, and RUN THE COMMAND ANYWAY (measured against
    codex-cli 0.147.0 — see docs/STATUS.md, "live-guard on codex"). That is also
    why `stop` below is Claude-only: `continue`/`stopReason` are exactly such
    unrecognized keys, so on codex the stop posture converts a working block into
    an execution while still recording "stopped": true.
    """
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}
    if stop:
        # Verified against @anthropic-ai/claude-code 2.1.156: `continue: false`
        # ends the run; `stopReason` is the message shown when it does.
        out["continue"] = False
        out["stopReason"] = reason
    print(json.dumps(out))
    sys.exit(0)


def fail(event: dict, stage: str, error: str) -> None:
    """One error path, both postures. Always logs; never raises."""
    closed = fail_closed()
    engine = f"error:{stage}"
    record({"guard": HOOK_VERSION, "outcome": "deny" if closed else "allow",
            "engine": engine, "stage": stage, "error": error,
            "failure_mode": "closed" if closed else "open",
            "tool_name": event.get("tool_name"), "session_id": event.get("session_id"),
            "tool_use_id": event.get("tool_use_id"), "event": event})
    touch_liveness(event.get("session_id") or "unknown",
                   "deny" if closed else "allow", engine, event)
    if closed:
        # An engine outage is not a policy violation: never an incident, never a stop.
        deny(f"Blocked by live-guard: the Cedar authorization engine is unavailable "
             f"({stage}: {error}). Failure mode is 'closed', so the call is denied.",
             stop=False)
    allow()


def main() -> None:
    started = time.time()
    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("event is not a JSON object")
    except Exception as exc:
        fail({"raw_stdin": raw[:2000]}, "stdin", str(exc))
        return

    if _truthy(_env("CEDAR_LIVE_GUARD_DISABLE")):
        record({"guard": HOOK_VERSION, "outcome": "allow", "engine": "disabled",
                "tool_name": event.get("tool_name"), "session_id": event.get("session_id")})
        touch_liveness(event.get("session_id") or "unknown", "allow", "disabled", event)
        allow()
        return

    try:
        requests = normalize_all(event)
    except Exception as exc:
        fail(event, "normalize", str(exc))
        return

    if not requests:
        # Only `apply_patch` can normalize to nothing: an envelope naming no file
        # operation. `base.cedar` is default-permit, so evaluating zero requests
        # would allow the call unexamined -- refuse instead of consenting by
        # omission. This is a policy refusal, not an engine outage, so unlike
        # `fail()` it IS an incident and DOES honour the stop posture.
        stop = on_deny() == "stop"
        entry = {"guard": HOOK_VERSION, "outcome": "deny", "engine": "envelope",
                 "decision": "Deny", "requests": 0,
                 "elapsed_ms": round((time.time() - started) * 1000, 1),
                 "tool_name": event.get("tool_name"),
                 "session_id": event.get("session_id"),
                 "tool_use_id": event.get("tool_use_id"), "event": event}
        record(entry)
        touch_liveness(event.get("session_id") or "unknown", "deny", "envelope", event)
        record_incident({"guard": HOOK_VERSION, "session_id": event.get("session_id"),
                         "tool_use_id": event.get("tool_use_id"),
                         "tool_name": event.get("tool_name"),
                         "determining": ["unparseable-envelope"],
                         "cedar_request": None, "event": event, "stopped": stop})
        deny("Blocked by live-guard: this apply_patch call declares no file "
             "operation the guard can authorize (empty or unparseable V4A "
             "envelope). Nothing was applied. Re-send the edit as a well-formed "
             "patch; do not work around the guard.", stop=stop)
        return

    # A patch touches many files but Cedar decides one action at a time, so every
    # request is evaluated and ANY deny wins. Evaluation stops at the first deny:
    # the call is refused whole, so later verdicts cannot change the outcome.
    request, out = requests[0], None
    for request in requests:
        try:
            out = run_decide(request)
        except subprocess.TimeoutExpired as exc:
            fail(event, "timeout", str(exc))
            return
        except Exception as exc:
            fail(event, "decide", str(exc))
            return
        if out["decision"] == "Deny":
            break

    decision = out["decision"]
    entry = {
        "guard": HOOK_VERSION,
        "outcome": "deny" if decision == "Deny" else "allow",
        "engine": "cedar",
        "decision": decision,
        "determining": out.get("determining"),
        "policy_count": out.get("policy_count"),
        "policy_dir": str(policy_dir()),
        "elapsed_ms": round((time.time() - started) * 1000, 1),
        "tool_name": event.get("tool_name"),
        "session_id": event.get("session_id"),
        "tool_use_id": event.get("tool_use_id"),
        # The DECIDING request: the one that denied, or the last one evaluated.
        # `requests` says how many the call implied, so a one-line decision log
        # still shows that a patch was multi-file.
        "cedar_request": request,
        "requests": len(requests),
        "event": event,
    }
    record(entry)
    touch_liveness(event.get("session_id") or "unknown", entry["outcome"], "cedar", event)

    if decision == "Deny":
        posture = on_deny()
        # `posture` is what the guard ASKED for. It deliberately replaces the old
        # `stopped` boolean, which asserted an outcome the hook cannot observe --
        # it read `true` whenever the posture was `stop`, including on codex where
        # the run demonstrably continued. Whether the agent complied is settled
        # from this session's decision log by `checks/live-guard-enforced.py`.
        record_incident({"guard": HOOK_VERSION, "session_id": event.get("session_id"),
                         "tool_use_id": event.get("tool_use_id"),
                         "tool_name": event.get("tool_name"),
                         "determining": out.get("determining"),
                         "cedar_request": request, "event": event, "posture": posture})
        deny(deny_reason(out, request, posture), stop=posture == "stop")
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # last resort: honour the posture, never crash
        record({"guard": HOOK_VERSION, "engine": "error:unhandled", "error": str(exc)})
        try:
            closed = fail_closed()
        except Exception:
            closed = False
        if closed:
            deny("Blocked by live-guard: unhandled guard error "
                 f"({exc}). Failure mode is 'closed', so the call is denied.")
        print("{}")
        sys.exit(0)

#!/usr/bin/env python3
"""Trajectory normalizer + claim matcher for general honesty attestation.

Reads an agent's `agent-stdio.log` (the harness's trusted record of what the
agent actually executed) and produces typed action records that a check can
attest declared claims against. Pure, stdlib-only (Python 3 + PyYAML contract).

Ports the argv-parsing primitives from the code-review protocol's `_crypto.py`
deliberately — the engine is upstream of protocols and must not import from one.
"""
import hashlib
import json
import os
import re
import shlex
import sys


def sha256_hex(text):
    """sha256 over the exact bytes of `text` (UTF-8, no added trailing newline),
    so it equals the agent's `printf '%s' "$out" | sha256sum`."""
    return hashlib.sha256((text if isinstance(text, str) else "").encode("utf-8")).hexdigest()


def _unwrap_shell(command):
    """If `command` is a `bash`/`sh` `-c`/`-lc` wrapper, return the wrapped
    inner command string; otherwise return `command` unchanged."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return command
    if len(argv) >= 3 and os.path.basename(argv[0]) in ("bash", "sh") and argv[1] in ("-c", "-lc"):
        return argv[2]
    return command


def _tokenize(command):
    """shlex the command into argv; [] if it can't be tokenized."""
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _str_or_empty(v):
    return v if isinstance(v, str) else ""


def normalize_codex(stdio_log_text):
    """codex `command_execution` JSONL -> [command action_record]. Mixed
    plain-text + JSONL log; each line parsed independently, non-JSON skipped."""
    records = []
    for line in (stdio_log_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "item.completed":
            continue
        item = rec.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        argv = _tokenize(_unwrap_shell(command)) if isinstance(command, str) else []
        records.append({
            "kind": "command",
            "tool": "command_execution",
            "argv": argv,
            "resource": command if isinstance(command, str) else "",
            "output": _str_or_empty(item.get("aggregated_output")),
            "exit_code": _int_or_none(item.get("exit_code")),
            "source_runtime": "codex",
        })
    return records


def normalize(stdio_log_text, source_runtime):
    """Dispatch to the per-runtime adapter. Unknown runtime -> []."""
    if source_runtime == "codex":
        return normalize_codex(stdio_log_text)
    if source_runtime == "claude":
        return normalize_claude(stdio_log_text)
    return []


_MCP_WRITE_RE = re.compile(r"(create|update|add|write|merge|comment|delete|push)", re.I)
_MCP_READ_RE = re.compile(r"(get|list|read|search|diff|status|view)", re.I)


def _mcp_kind(name):
    """Conservative engine-owned MCP op class (§10.3, tunable). None = don't
    surface (fail closed on unknown ops rather than guess an effect class)."""
    n = name or ""
    if _MCP_WRITE_RE.search(n):
        return "file_write"
    if _MCP_READ_RE.search(n):
        return "file_read"
    return None


def _claude_tool_use_record(name, input_obj):
    """One Claude tool_use -> a partial action_record (result attached later),
    or None to skip (Task/WebSearch/unknown)."""
    input_obj = input_obj if isinstance(input_obj, dict) else {}
    if name == "Bash":
        cmd = input_obj.get("command") or ""
        return {"kind": "command", "tool": "Bash", "argv": _tokenize(_unwrap_shell(cmd)),
                "resource": cmd, "output": "", "exit_code": None, "source_runtime": "claude"}
    if name in ("Write", "Edit", "NotebookEdit"):
        path = input_obj.get("file_path") or input_obj.get("notebook_path") or ""
        return {"kind": "file_write", "tool": name, "argv": None,
                "resource": path, "output": "", "exit_code": None, "source_runtime": "claude"}
    if name == "Read":
        return {"kind": "file_read", "tool": "Read", "argv": None,
                "resource": input_obj.get("file_path") or "", "output": "",
                "exit_code": None, "source_runtime": "claude"}
    if name == "WebFetch":
        return {"kind": "fetch", "tool": "WebFetch", "argv": None,
                "resource": input_obj.get("url") or "", "output": "",
                "exit_code": None, "source_runtime": "claude"}
    if str(name).startswith("mcp__"):
        kind = _mcp_kind(name)
        if kind is None:
            return None  # unknown MCP op -> don't guess an effect class (§10.3)
        return {"kind": kind, "tool": name, "argv": None,
                "resource": name, "output": "", "exit_code": None, "source_runtime": "claude"}
    return None  # Task, WebSearch, unknown -> invisible (§7)


def _claude_result_facts(block):
    """Extract (output, exit_code) from a tool_result block. `is_error` maps to
    a non-zero exit; text content is flattened to a string."""
    content = block.get("content")
    if isinstance(content, list):
        text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    else:
        text = content if isinstance(content, str) else ""
    exit_code = 1 if block.get("is_error") else 0
    return text, exit_code


def normalize_claude(stdio_log_text):
    """Claude stream-json (mixed with --debug-file plain text) -> records.
    Correlates each tool_use with its later tool_result by tool_use_id."""
    by_id = {}
    order = []
    for line in (stdio_log_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                tid = c.get("id")
                if tid is None:
                    continue  # id-less tool_use: off-contract, would collide/mis-attribute
                rec = _claude_tool_use_record(c.get("name"), c.get("input"))
                if rec is not None:
                    by_id[tid] = rec
                    order.append(tid)
            elif c.get("type") == "tool_result":
                tid = c.get("tool_use_id")
                if tid in by_id:
                    out, code = _claude_result_facts(c)
                    by_id[tid]["output"] = out
                    by_id[tid]["exit_code"] = code
    return [by_id[t] for t in order]


def match_claim(records, claim):
    """Resolve a claim's selector against the trajectory records.
    Returns {"status","record","count"}. Ambiguity fails closed unless the
    claim declares ambiguity:"newest" (§6)."""
    kind = claim.get("kind")
    sel = claim.get("selector") or {}
    matches = []
    for r in records:
        if not isinstance(r, dict) or r.get("kind") != kind:
            continue
        if kind == "command":
            argv = r.get("argv") or []
            if not argv:
                continue
            if os.path.basename(argv[0]) != sel.get("argv0"):
                continue
            rest = argv[1:]
            if not all(tok in rest for tok in (sel.get("args_contain") or [])):
                continue
            matches.append(r)
        elif kind == "file_write":
            if r.get("resource") == sel.get("path"):
                matches.append(r)
    if not matches:
        return {"status": "none", "record": None, "count": 0}
    if len(matches) > 1:
        if claim.get("ambiguity") == "newest":
            return {"status": "matched", "record": matches[-1], "count": len(matches)}
        return {"status": "ambiguous", "record": None, "count": len(matches)}
    return {"status": "matched", "record": matches[0], "count": 1}


def attest_command(record, attested):
    """Compare a command claim's `attested` against the trajectory record.
    Rebuilds exit_code/output from the record; recomputes and compares the hash
    (preserve-and-compare). Empty output -> expected hash is null."""
    attested = attested if isinstance(attested, dict) else {}
    if "exit_code" not in attested and "output_sha256" not in attested:
        return {"ok": False,
                "reason": "attest command claim asserts no checkable fact (need exit_code and/or output_sha256)"}
    reasons = []
    if "exit_code" in attested and record.get("exit_code") != attested["exit_code"]:
        reasons.append(f"exit_code: claimed {attested['exit_code']!r}, actual {record.get('exit_code')!r}")
    if "output_sha256" in attested:
        output = record.get("output") or ""
        expected = sha256_hex(output) if output else None
        claimed = attested["output_sha256"]
        claimed = claimed.lower() if isinstance(claimed, str) else claimed
        if claimed != expected:
            reasons.append("output_sha256 does not match sha256(actual output) — fabricated/incorrect")
    return {"ok": not reasons, "reason": "; ".join(reasons)}


def attest_file_write(record, attested):
    """A matched file_write record proves the write occurred. The claim must
    assert `written: true` (an explicit positive attestation)."""
    attested = attested if isinstance(attested, dict) else {}
    if attested.get("written") is True:
        return {"ok": True, "reason": ""}
    return {"ok": False, "reason": "file_write claim must assert `written: true`"}


_SECRET_PATH_RE = re.compile(r"(^|/)\.env|\.pem$|\.key$|credentials|secret", re.I)
_HARNESS_INTERNAL_RE = re.compile(r"^/tmp/gh-aw/")


def _is_harness_internal(path):
    return bool(_HARNESS_INTERNAL_RE.search(path or ""))


def classify_action(record):
    """Classify a DISTINCT-TOOL-CALL record into a Layer-B effectful class, or
    None (benign / exempt / out-of-scope). A command/Bash record is ALWAYS None
    — its internals are the disclosed shell blind spot (spec §2)."""
    if not isinstance(record, dict):
        return None
    kind = record.get("kind")
    resource = record.get("resource") or ""
    if kind == "fetch":                                   # WebFetch / MCP egress
        return {"class": "egress", "resource": resource}
    if kind == "file_write":                              # Write / Edit / MCP write
        return None if _is_harness_internal(resource) else {"class": "file_write", "resource": resource}
    if kind == "file_read":                               # Read / MCP read
        return {"class": "secret_read", "resource": resource} if _SECRET_PATH_RE.search(resource) else None
    return None                                           # command, unknown -> blind spot / benign


def _cli(argv):
    log_path = argv[1] if len(argv) > 1 else ""
    runtime = argv[2] if len(argv) > 2 else "codex"
    try:
        with open(log_path) as fh:
            text = fh.read()
    except OSError:
        text = ""
    sys.stdout.write(json.dumps(normalize(text, runtime)))


if __name__ == "__main__":
    _cli(sys.argv)

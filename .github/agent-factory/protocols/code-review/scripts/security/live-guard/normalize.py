#!/usr/bin/env python3
"""normalize.py — a Claude Code `PreToolUse` event -> a Cedar authorization request.

The ONLY new logic of the live guard. Pure and importable: `normalize(event)` takes
the raw hook payload (a dict) and returns the request dict that `decide.js` hands
to `_cedar-decide.js`. No I/O, no subprocess, no Node — so every mapping is
unit-testable on its own.

Vocabulary: the vendored `Sondera::…` schema (`policy/cedar/live/base.cedarschema`).
That schema is the CONTRACT for the shapes built here; it is deliberately not
loaded at evaluation time (Cedar evaluates without a schema and `entities` is
`[]`). Distinct from the plan path's bare `Action::…` vocabulary in
`run-cedar-plan.js`, which is untouched by this module.

Tool -> action mapping:

    Bash                            -> ShellCommand   (resource Trajectory)
    Read                            -> FileRead       (resource File)
    Write                           -> FileWrite      (resource File)
    Edit, NotebookEdit              -> FileEdit       (resource File)
    WebFetch                        -> WebFetch       (resource Trajectory)
    Grep, Glob, Task, mcp__*, other -> PreToolUse     (resource Tool)

`tool_input.description` is model-authored prose about the model's own intent and
is NEVER a policy input: it is not copied into any context field.
"""
from __future__ import annotations

import json
import posixpath
from urllib.parse import urlsplit

__all__ = [
    "normalize",
    "path_normalized",
    "url_parse",
    "SIGNATURE_STUB",
    "POLICY_STUB",
    "LABEL_STUB",
    "PARSE_STUB",
    "ACTION_BY_TOOL",
    "PRINCIPAL_TYPE",
]

# --- neutral stubs ---------------------------------------------------------
# The schema declares context members this phase does not populate. Supplying
# constants (rather than omitting the members) is what sondera itself does when
# its guardrails are disabled, and it keeps the vendored policies drop-in: adding
# YARA/IFC later becomes a data change, not a schema migration.
#
#   signature — YARA scan results (phase: out of scope; see spec "Out of scope")
#   policy    — LLM policy-model classification (never on the blocking path)
#   label     — IFC data-sensitivity label (phase B2 needs entity data)
SIGNATURE_STUB = {"matches": 0, "categories": [], "severity": 0}
POLICY_STUB = {"compliant": True, "violations": []}
# A Cedar entity reference inside a context record is encoded `{"__entity": …}`.
# `Label::"Public"` is the schema's bottom label. No vendored live policy reads
# it, so it is inert — carried for schema fidelity only.
LABEL_STUB = {"__entity": {"type": "Sondera::Label", "id": "Public"}}

# `parse` is the tree-sitter-bash view of a shell command (phase B3). `ok: False`
# is the CORRECT starting value, not a placeholder: the vendored policies branch on
# `parse.ok` and fall back to conservative `context.command like` matching, so a
# real parser later only tightens decisions. Every set member is present because a
# policy may read it once `ok` is true.
PARSE_STUB = {
    "ok": False,
    "programs": [],
    "program_flags": [],
    "program_flags_normalized": [],
    "program_args": [],
    "program_args_normalized": [],
    "program_arg_path_components_normalized": [],
    "has_dynamic_command": False,
    "has_command_substitution": False,
    "has_pipeline": False,
    "has_redirection": False,
}

PRINCIPAL_TYPE = "Sondera::Agent"
_NS = "Sondera::Action"

#: tool name -> Cedar action id. Anything absent falls through to "PreToolUse".
ACTION_BY_TOOL = {
    "Bash": "ShellCommand",
    "Read": "FileRead",
    "Write": "FileWrite",
    "Edit": "FileEdit",
    "NotebookEdit": "FileEdit",
    "WebFetch": "WebFetch",
}

#: Cedar action id -> (resource entity type, file-operation label or None)
_RESOURCE_TYPE = {
    "ShellCommand": "Sondera::Trajectory",
    "WebFetch": "Sondera::Trajectory",
    "FileRead": "Sondera::File",
    "FileWrite": "Sondera::File",
    "FileEdit": "Sondera::File",
    "FileDelete": "Sondera::File",
    "PreToolUse": "Sondera::Tool",
}
_OPERATION = {"FileRead": "Read", "FileWrite": "Write", "FileEdit": "Edit", "FileDelete": "Delete"}

# tool_input keys that carry a path, most specific first.
_PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")

_DEFAULT_PORTS = {"http": 80, "https": 443, "ftp": 21, "ws": 80, "wss": 443}


def path_normalized(path: str) -> str:
    """Lexical companion to `path`, per the schema's definition.

    Folds ``\\`` to ``/``, collapses repeated ``/``, lowercases. Lexical ONLY: no
    canonicalization, no symlink resolution, no variable expansion, and ``..`` is
    NOT erased (erasing it would let `a/../../etc/shadow` normalize into an
    allow-shaped string). Windows verbatim/device prefixes (``\\\\?\\``) are out of
    scope for this POSIX-hosted guard.
    """
    if not isinstance(path, str):
        return ""
    out = path.replace("\\", "/")
    while "//" in out:
        out = out.replace("//", "/")
    return out.lower()


def _abs_path(raw: str, cwd: str) -> str:
    """Make a tool-supplied path absolute *lexically*, so that `*/.ssh/id_*`-shaped
    policies see the same string whether the agent passed an absolute or a
    workspace-relative path.

    Lexical join only — nothing is resolved on disk, so `normalize()` stays pure.
    `~` is left literal (no `expanduser`, which would read the hook runner's own
    environment): the credential-path policies all lead with `*/`, which matches
    `~/.ssh/id_rsa` unchanged.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    if raw.startswith("/") or raw.startswith("~"):
        return raw
    if isinstance(cwd, str) and cwd:
        return posixpath.join(cwd, raw)
    return raw


def url_parse(url: str) -> dict:
    """Structured parse of a URL, per the schema's `UrlParseContext`.

    Parses the HOST rather than globbing the raw string: Cedar's `like` wildcard
    matches ``/``, ``@`` and ``:``, so ``https://trusted.example@evil.com``
    satisfies a ``*.example`` allowlist while connecting to evil.com. Parsing
    resolves that to host ``evil.com``.
    """
    if not isinstance(url, str) or not url:
        return {"ok": False, "scheme": "", "host": "", "port": 0}
    try:
        parts = urlsplit(url)
    except ValueError:
        return {"ok": False, "scheme": "", "host": "", "port": 0}
    scheme = (parts.scheme or "").lower()
    if not scheme:  # relative / scheme-relative URL -> not parseable as absolute
        return {"ok": False, "scheme": "", "host": "", "port": 0}
    try:
        host = (parts.hostname or "").lower()
        explicit = parts.port
    except ValueError:  # malformed port
        return {"ok": True, "scheme": scheme, "host": "", "port": 0}
    # IPv6 hosts keep their brackets, per the schema.
    if host and ":" in host:
        host = "[%s]" % host
    port = explicit if explicit else _DEFAULT_PORTS.get(scheme, 0)
    return {"ok": True, "scheme": scheme, "host": host, "port": int(port)}


def _base_context(cwd: str) -> dict:
    return {
        "workspace": {"cwd": cwd},
        "signature": dict(SIGNATURE_STUB),
        "policy": dict(POLICY_STUB),
        "label": dict(LABEL_STUB),
    }


def normalize(event: dict) -> dict:
    """PreToolUse event dict -> Cedar request dict.

    Returns ``{"principal", "action", "resource", "context"}`` with principal /
    action / resource as ``{"type", "id"}`` objects (not ``Type::"id"`` strings —
    an id containing a quote would not round-trip through the string form).
    """
    if not isinstance(event, dict):
        event = {}
    tool = event.get("tool_name")
    tool = tool if isinstance(tool, str) else ""
    tool_input = event.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    cwd = event.get("cwd")
    cwd = cwd if isinstance(cwd, str) else ""
    session = event.get("session_id")
    session = session if isinstance(session, str) and session else "unknown-session"

    action = ACTION_BY_TOOL.get(tool, "PreToolUse")
    context = _base_context(cwd)

    if action == "ShellCommand":
        command = tool_input.get("command")
        context["command"] = command if isinstance(command, str) else ""
        context["working_dir"] = cwd
        context["parse"] = dict(PARSE_STUB)
        # Declared by the schema, always empty by design: a shell command only
        # NAMES paths, and nothing here reads them.
        context["file_signature"] = dict(SIGNATURE_STUB)
        resource_id = session
    elif action in ("FileRead", "FileWrite", "FileEdit", "FileDelete"):
        raw = next((tool_input[k] for k in _PATH_KEYS
                    if isinstance(tool_input.get(k), str)), "")
        abs_path = _abs_path(raw, cwd)
        context["path"] = abs_path
        context["path_normalized"] = path_normalized(abs_path)
        context["operation"] = _OPERATION[action]
        resource_id = abs_path or raw or tool
    elif action == "WebFetch":
        url = tool_input.get("url")
        url = url if isinstance(url, str) else ""
        context["url"] = url
        # The schema's WebFetch context declares `prompt`; it is model-authored
        # text, carried for schema fidelity and never matched by a live policy.
        prompt = tool_input.get("prompt")
        context["prompt"] = prompt if isinstance(prompt, str) else ""
        context["url_parse"] = url_parse(url)
        resource_id = session
    else:  # generic fallthrough: Grep / Glob / Task / mcp__* / anything new
        context["tool"] = tool
        # Canonical JSON so the same call always serializes the same way.
        context["arguments"] = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
        resource_id = tool or "unknown"

    return {
        "principal": {"type": PRINCIPAL_TYPE, "id": "claude-code"},
        "action": {"type": _NS, "id": action},
        "resource": {"type": _RESOURCE_TYPE[action], "id": resource_id},
        "context": context,
    }


# --- codex `apply_patch` -----------------------------------------------------
#
# codex sends every file edit as ONE tool call whose `tool_input.command` holds a
# V4A patch envelope, which may describe SEVERAL files. Cedar evaluates one action
# per request, so the envelope fans out into one request per file operation.
#
# Only the header lines are read. Every File* policy in the corpus matches on
# `context.path_normalized` alone, so hunk bodies carry nothing a policy can use
# and are deliberately skipped -- this is a header scan, not a diff parser.

#: V4A header prefix -> Cedar action id. `*** Move to:` is NOT here: a rename
#: writes a new path AND removes the old one, so it expands to two operations.
PATCH_OP_ACTION = {
    "*** Add File: ": "FileWrite",
    "*** Update File: ": "FileEdit",
    "*** Delete File: ": "FileDelete",
}
_MOVE_TO = "*** Move to: "


def parse_patch_ops(command: str) -> list[tuple[str, str]]:
    """V4A envelope -> [(cedar action id, path), ...] in envelope order.

    Paths in a V4A patch are relative by grammar; resolving them against `cwd` is
    the caller's job (`_abs_path`), exactly as for a Claude file tool.
    """
    ops: list[tuple[str, str]] = []
    if not isinstance(command, str):
        return ops
    updating = ""   # path of the `*** Update File:` a `*** Move to:` renames
    for line in command.splitlines():
        if line.startswith(_MOVE_TO):
            # A rename is a write at the destination and a removal at the source.
            # Authorizing only the destination would let a rename delete a
            # protected file without any policy ever seeing the deletion.
            if updating:
                ops.append(("FileDelete", updating))
            ops.append(("FileWrite", line[len(_MOVE_TO):].strip()))
            continue
        for prefix, action in PATCH_OP_ACTION.items():
            if line.startswith(prefix):
                path = line[len(prefix):].strip()
                ops.append((action, path))
                updating = path if action == "FileEdit" else ""
                break
    return ops


def _file_request(action: str, raw_path: str, cwd: str) -> dict:
    """One Cedar request for one file operation, shaped like `normalize`'s."""
    context = _base_context(cwd)
    abs_path = _abs_path(raw_path, cwd)
    context["path"] = abs_path
    context["path_normalized"] = path_normalized(abs_path)
    context["operation"] = _OPERATION[action]
    return {
        "principal": {"type": PRINCIPAL_TYPE, "id": "claude-code"},
        "action": {"type": _NS, "id": action},
        "resource": {"type": _RESOURCE_TYPE[action], "id": abs_path or raw_path},
        "context": context,
    }


def normalize_all(event: dict) -> list[dict]:
    """Every Cedar request one hook event implies.

    One request for every tool except `apply_patch`, which yields one per file
    operation in its envelope. An envelope naming no file operation yields an
    EMPTY list: the caller must treat that as a refusal, never as consent --
    `base.cedar` is default-permit, so zero requests means zero forbids.
    """
    if not isinstance(event, dict):
        event = {}
    if event.get("tool_name") != "apply_patch":
        return [normalize(event)]
    tool_input = event.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    cwd = event.get("cwd")
    cwd = cwd if isinstance(cwd, str) else ""
    return [_file_request(action, path, cwd)
            for action, path in parse_patch_ops(tool_input.get("command"))]


if __name__ == "__main__":  # tiny CLI: event JSON on stdin -> request JSON on stdout
    import sys

    json.dump(normalize(json.load(sys.stdin)), sys.stdout)
    sys.stdout.write("\n")

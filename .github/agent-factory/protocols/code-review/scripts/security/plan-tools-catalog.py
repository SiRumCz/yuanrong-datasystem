#!/usr/bin/env python3
"""Emit the plan tool registry — the canonical vocabulary an agent MUST use when naming
plan_ast steps, so plans are GROUNDED in a predefined tool set (the paper's "assuming a
predefined set of tools") instead of inventing tool names.

Derived from the shared plan-tool-aliases.json (the single source shared with the
guardians/cedar classifiers), so adding a category there flows here automatically. Stdlib
only (json) — runs in a pre-agent step before any toolchain install. Prints markdown to
stdout; the agent reads it and picks one tool per step.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ALIASES = os.path.join(HERE, "plan-tool-aliases.json")

# canonical registry (stable security categories) — desc + declared args for each.
REGISTRY = [
    ("read_repo_file", "read a repo / PR / input file", ["path"]),
    ("read_secret",    "read a secret or credential (.env, .pem, .key, token)", ["path"]),
    ("read_external",  "read untrusted / external input (web search results, fetched content)", ["path"]),
    ("write_file",     "write a file", ["path", "content"]),
    ("run_command",    "run a shell command", ["argv"]),
    ("network_send",   "send data over the network to a host", ["host", "body"]),
    ("publish",        "post to an external channel (GitHub issue / PR / comment, uploaded file)", ["channel", "body"]),
    ("compute",        "internal computation or reasoning; performs NO input/output", []),
]


def main():
    try:
        aliases = json.load(open(ALIASES)).get("aliases", {})
    except (OSError, ValueError):
        aliases = {}
    print("# Plan tool registry — name every `plan_ast` step with ONE tool from THIS list.")
    print("# Do NOT invent tool names. Pick the tool matching what the step does; use")
    print("# `compute` for any pure-reasoning / no-I/O step. Use the arg names shown.\n")
    for name, desc, args in REGISTRY:
        arghint = f"  args: {{{', '.join(args)}}}" if args else ""
        egs = aliases.get(name, [])[:4]
        aka = f"  (your `readFile`/`curlPost`/… map here, e.g. {', '.join(egs)})" if egs else ""
        print(f"- {name:14} — {desc}{arghint}{aka}")


if __name__ == "__main__":
    main()

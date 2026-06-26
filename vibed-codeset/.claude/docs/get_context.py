#!/usr/bin/env python3
"""get_context.py — query the repository knowledge base before editing a file.

Usage:
    python .claude/docs/get_context.py <path>      # context for one source file
    python .claude/docs/get_context.py .           # repo-level overview (root)
    python .claude/docs/get_context.py --list       # list files that have context

The knowledge base (knowledge.json) is generated offline by mining git history,
static analysis (constructs + caller graph), test coverage, and co-change
relationships, then synthesizing per-file insights. This script only renders it.

Standalone: depends on the Python standard library only.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KB_PATH = os.path.join(HERE, "knowledge.json")


def _load() -> dict:
    try:
        with open(KB_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        sys.stderr.write(f"knowledge base not found at {KB_PATH}\n")
        sys.exit(1)


def _normalize(arg: str, files: dict) -> str | None:
    """Resolve a user-supplied path to a key in the knowledge base."""
    if arg in files:
        return arg
    arg = arg.replace("\\", "/").lstrip("./")
    if arg in files:
        return arg
    # Try suffix match (handles absolute paths like /repo/pkg/mod.py).
    candidates = [k for k in files if arg.endswith(k) or k.endswith(arg)]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        # Prefer exact basename match.
        base = os.path.basename(arg)
        exact = [k for k in candidates if os.path.basename(k) == base]
        if len(exact) == 1:
            return exact[0]
    return None


def _bullets(items, prefix="- "):
    return "\n".join(f"{prefix}{x}" for x in items)


def render_file(rec: dict) -> str:
    out: list[str] = [f"# {rec['path']}", ""]

    if rec.get("description"):
        out += ["### Description", rec["description"].strip(), ""]

    ck = rec.get("edit_checklist") or {}
    if ck.get("tests_to_run") or ck.get("data_constants") or ck.get("warning"):
        out.append("### Edit Checklist")
        if ck.get("tests_to_run"):
            out.append("Tests to run: " + ", ".join(f"`{t}`" for t in ck["tests_to_run"]))
        if ck.get("data_constants"):
            out.append("Data/constants: " + ", ".join(f"`{c}`" for c in ck["data_constants"]))
        if ck.get("warning"):
            out.append(f"WARNING: {ck['warning']}")
        out.append("")

    insights = rec.get("historical_insights") or []
    if insights:
        out.append("### Historical Insights")
        for ins in insights:
            out.append(f"- [{ins.get('category','General')}] {ins.get('title','')}")
            if ins.get("problem"):
                out.append(f"  Problem: {ins['problem']}")
            if ins.get("root_cause"):
                out.append(f"  Root cause: {ins['root_cause']}")
            if ins.get("solution"):
                out.append(f"  Solution: {ins['solution']}")
            if ins.get("commits"):
                out.append("  Commits: " + ", ".join(f"`{c}`" for c in ins["commits"]))
            if ins.get("constructs"):
                out.append("  Constructs: " + ", ".join(f"`{c}`" for c in ins["constructs"]))
        out.append("")

    constructs = rec.get("key_constructs") or []
    if constructs:
        out.append("### Key Constructs")
        for c in constructs:
            desc = c.get("rationale") or c.get("docstring") or c.get("signature") or ""
            desc = desc.splitlines()[0] if desc else ""
            out.append(f"- **{c['name']}** ({c.get('kind','symbol')}): {desc}".rstrip())
            for caller in c.get("callers", [])[:8]:
                lines = ", ".join(str(n) for n in caller["lines"][:14])
                out.append(f"  - `{caller['file']}`: called at lines {lines}")
        out.append("")

    tests = rec.get("tests") or {}
    if tests.get("files") or tests.get("functions"):
        out.append("### Tests")
        if tests.get("files"):
            out.append("Files: " + ", ".join(f"`{t}`" for t in tests["files"][:18]))
        if tests.get("functions"):
            out.append("Functions: " + ", ".join(f"`{t}`" for t in tests["functions"][:18]))
        out.append("")

    related = rec.get("related_files") or []
    if related:
        out.append("### Related Files")
        for r in related:
            line = f"- `{r['file']}` [co-change × {r.get('shared_commits','?')}]"
            if r.get("relation"):
                line += f" | Rel: {r['relation']}"
            if r.get("check"):
                line += f" | Check: {r['check']}"
            out.append(line)
        out.append("")

    sem = rec.get("semantic_overview") or {}
    if sem.get("tags") or sem.get("entities") or sem.get("capabilities"):
        out.append("### Semantic Overview")
        if sem.get("tags"):
            out.append("Tags: " + ", ".join(f"`{t}`" for t in sem["tags"]))
        if sem.get("entities"):
            out.append("Entities: " + ", ".join(f"`{e}`" for e in sem["entities"]))
        for cap in sem.get("capabilities", []):
            out.append(f"- {cap}")
        out.append("")

    pitfalls = rec.get("pitfalls") or []
    if pitfalls:
        out.append("### Pitfalls")
        for p in pitfalls:
            out.append(f"- {p.get('description','')}")
            if p.get("consequence"):
                out.append(f"  Consequence: {p['consequence']}")
            if p.get("prevention"):
                out.append(f"  Prevention: {p['prevention']}")
        out.append("")

    rg = rec.get("reading_guide") or {}
    if rg.get("start") or rg.get("key") or rg.get("skip"):
        out.append("### Reading Guide")
        if rg.get("start"):
            out.append(f"Start: `{rg['start']}`")
        if rg.get("key"):
            out.append("Key: " + "; ".join(rg["key"]))
        if rg.get("skip"):
            out.append(f"Skip: {rg['skip']}")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def render_root(repo: dict) -> str:
    out = ["# Folder: (root)", "", "### Description", repo.get("architecture", "") or repo.get("one_liner", ""), ""]
    if repo.get("key_files"):
        out.append("### Key Files")
        for kf in repo["key_files"]:
            out.append(f"- `{kf['path']}`: {kf.get('role','')}")
        out.append("")
    if repo.get("key_behaviors"):
        out += ["### Key Behaviors", _bullets(repo["key_behaviors"]), ""]
    if repo.get("gotchas"):
        out += ["### Gotchas", _bullets(repo["gotchas"]), ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str]) -> int:
    kb = _load()
    files = kb.get("files", {})

    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write(__doc__ or "")
        return 0
    if argv[0] == "--list":
        for k in sorted(files):
            print(k)
        return 0

    arg = argv[0]
    # Root / overview request.
    if arg in (".", "/", "root") or os.path.basename(arg.rstrip("/")) in ("", "."):
        sys.stdout.write(render_root(kb.get("repo", {})))
        return 0

    key = _normalize(arg, files)
    if key is None:
        # No per-file record: fall back to the repo overview so the agent still
        # gets oriented instead of nothing.
        sys.stderr.write(f"(no specific context for {arg}; showing repo overview)\n")
        sys.stdout.write(render_root(kb.get("repo", {})))
        return 0

    sys.stdout.write(render_file(files[key]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

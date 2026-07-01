#!/usr/bin/env python3
"""Convert custody/context-viewer SessionExport JSON to engine evidence.

Usage:
  to-evidence.py [session-export.json] [evidence.json]
  to-evidence.py --session-export session-export.json --output evidence.json

The adapter is deterministic: it reads phase/token data from the assembled
SessionExport and does not classify transcript content itself.
"""
import argparse
import json
import os
import sys
from collections import OrderedDict

PHASES = (
    "UNDERSTAND",
    "EXPLORE",
    "ANALYZE",
    "PLAN",
    "IMPLEMENT",
    "VERIFY",
    "COMPLETE",
)
PHASE_SET = set(PHASES)
DEFAULT_SESSION_EXPORT = "/tmp/gh-aw/session-export.json"


def is_non_negative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def count_value(value):
    if is_non_negative_int(value):
        return value
    return 0


def normalize_phase(value):
    if not isinstance(value, str):
        return None
    phase = value.strip().upper().split(".")[0]
    return phase if phase in PHASE_SET else None


def resolve_session_export(path):
    if not path:
        path = os.environ.get("SESSION_EXPORT_PATH") or DEFAULT_SESSION_EXPORT
    if not os.path.isdir(path):
        return path
    candidates = [
        os.path.join(path, "session-export.json"),
        os.path.join(path, "context-export", "session-export.json"),
    ]
    candidates.extend(
        os.path.join(path, name)
        for name in sorted(os.listdir(path))
        if name.endswith(".json")
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(path, "session-export.json")


def write_json(evidence, output_path):
    payload = json.dumps(evidence, separators=(",", ":"))
    if output_path:
        directory = os.path.dirname(os.path.abspath(output_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
    else:
        print(payload)


def error_evidence(path, summary):
    return {
        "transcript_present": False,
        "phases": [],
        "meta": {},
        "session_export": {
            "path": path,
            "error": True,
            "summary": summary,
        },
    }


def read_export(path):
    if not os.path.exists(path):
        return None, f"session export not found: {path}"
    if not os.path.isfile(path):
        return None, f"session export path is not a file: {path}"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, f"session export unreadable or invalid JSON: {type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return None, "session export JSON is not an object"
    return data, ""


def export_error_summary(export):
    error = export.get("error")
    if not error:
        return ""
    if isinstance(error, dict):
        summary = error.get("summary") or error.get("title")
        if isinstance(summary, str):
            return summary
        return json.dumps(error, sort_keys=True, separators=(",", ":"))
    if isinstance(error, str):
        return error
    return str(error)


def export_meta(export):
    meta = export.get("meta")
    if not isinstance(meta, dict):
        return {}
    out = {}
    pr_number = meta.get("pr_number")
    head_sha = meta.get("head_sha")
    if is_non_negative_int(pr_number):
        out["pr_number"] = pr_number
    if isinstance(head_sha, str):
        out["head_sha"] = head_sha
    return out


def iter_parts(export):
    for file_entry in export.get("files") or []:
        if not isinstance(file_entry, dict):
            continue
        conversation = file_entry.get("conversation")
        if not isinstance(conversation, dict):
            continue
        for message in conversation.get("messages") or []:
            if not isinstance(message, dict):
                continue
            for part in message.get("parts") or []:
                if isinstance(part, dict):
                    yield part


def first_component_tokens(export):
    analytics = export.get("analytics")
    if not isinstance(analytics, dict):
        return OrderedDict()
    comparisons = analytics.get("componentComparison")
    if not isinstance(comparisons, list) or not comparisons:
        return OrderedDict()
    first = comparisons[0]
    if not isinstance(first, dict):
        return OrderedDict()
    tokens = first.get("componentTokens")
    if not isinstance(tokens, dict):
        return OrderedDict()
    ordered = OrderedDict()
    for phase, token_count in tokens.items():
        normalized = normalize_phase(phase)
        if normalized and normalized not in ordered:
            ordered[normalized] = count_value(token_count)
    return ordered


def part_counts_by_phase(parts):
    counts = {}
    for part in parts:
        phase = normalize_phase(part.get("component"))
        if not phase:
            continue
        counts[phase] = counts.get(phase, 0) + 1
    return counts


def evidence_from_export(path):
    export, read_error = read_export(path)
    if read_error:
        return error_evidence(path, read_error)

    error_summary = export_error_summary(export)
    parts = list(iter_parts(export))
    transcript_present = bool(parts) and not error_summary
    session_info = {"path": path, "error": bool(error_summary)}
    if error_summary:
        session_info["summary"] = error_summary

    if not transcript_present:
        return {
            "transcript_present": False,
            "phases": [],
            "meta": export_meta(export),
            "session_export": session_info,
        }

    token_by_phase = first_component_tokens(export)
    message_count_by_phase = part_counts_by_phase(parts)
    phases = [
        {
            "phase": phase,
            "token_count": token_count,
            "message_count": message_count_by_phase.get(phase, 0),
        }
        for phase, token_count in token_by_phase.items()
    ]

    return {
        "transcript_present": True,
        "phases": phases,
        "meta": export_meta(export),
        "session_export": session_info,
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="[session-export.json] [evidence.json]")
    parser.add_argument("--session-export")
    parser.add_argument("--output", "-o")
    args = parser.parse_args(argv)
    if len(args.paths) > 2:
        parser.error("expected at most two positional paths")
    session_export = args.session_export or (args.paths[0] if args.paths else "")
    output = args.output or (args.paths[1] if len(args.paths) > 1 else "")
    return resolve_session_export(session_export), output


def main(argv):
    session_export, output = parse_args(argv)
    write_json(evidence_from_export(session_export), output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Check: context evidence has the SessionExport-derived evidence shape.

ABI: context-schema-valid.py <evidence.json> <diff.txt> <changed-files.txt>
Prints one {"check","pass","feedback"} object and always exits 0.
"""
import json
import sys

PHASES = {
    "UNDERSTAND",
    "EXPLORE",
    "ANALYZE",
    "PLAN",
    "IMPLEMENT",
    "VERIFY",
    "COMPLETE",
}


def is_non_negative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def emit(ok, feedback):
    print(json.dumps({"check": "context-schema-valid", "pass": ok, "feedback": feedback}))


def validate_phase(entry, index, seen):
    problems = []
    if not isinstance(entry, dict):
        return [f"phases[{index}] is not an object"]
    phase = entry.get("phase")
    if phase not in PHASES:
        problems.append(f"phases[{index}].phase {phase!r} is not one of {sorted(PHASES)}")
    elif phase in seen:
        problems.append(f"duplicate phase entry {phase!r}")
    else:
        seen.add(phase)
    for key in ("token_count", "message_count"):
        if key not in entry:
            problems.append(f"phases[{index}] missing `{key}`")
        elif not is_non_negative_int(entry.get(key)):
            problems.append(f"phases[{index}].{key} is not a non-negative integer")
    return problems


def validate_meta(meta):
    if meta is None:
        return ["missing `meta` object"]
    if not isinstance(meta, dict):
        return ["`meta` is not an object"]
    problems = []
    if "pr_number" in meta and not is_non_negative_int(meta.get("pr_number")):
        problems.append("meta.pr_number is not a non-negative integer")
    if "head_sha" in meta and not isinstance(meta.get("head_sha"), str):
        problems.append("meta.head_sha is not a string")
    return problems


def validate_session_export(session_export):
    if not isinstance(session_export, dict):
        return ["missing or non-object `session_export`"]
    problems = []
    if not isinstance(session_export.get("path"), str) or not session_export.get("path"):
        problems.append("session_export.path is missing or not a non-empty string")
    if not isinstance(session_export.get("error"), bool):
        problems.append("session_export.error is not boolean")
    if "summary" in session_export and not isinstance(session_export.get("summary"), str):
        problems.append("session_export.summary is not a string")
    return problems


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "", encoding="utf-8") as fh:
            evidence = json.load(fh)
    except (OSError, ValueError) as exc:
        emit(False, f"evidence unreadable/not JSON: {exc}")
        return

    problems = []
    if not isinstance(evidence, dict):
        problems.append("evidence is not a JSON object")
    else:
        transcript_present = evidence.get("transcript_present")
        if not isinstance(transcript_present, bool):
            problems.append("missing or non-boolean `transcript_present`")

        phases = evidence.get("phases")
        if not isinstance(phases, list):
            problems.append("missing or non-list `phases`")
            phases = []
        else:
            seen = set()
            for index, entry in enumerate(phases):
                problems.extend(validate_phase(entry, index, seen))

        problems.extend(validate_meta(evidence.get("meta")))
        session_export = evidence.get("session_export")
        problems.extend(validate_session_export(session_export))

        if transcript_present is False and phases:
            problems.append("transcript_present is false but phases is non-empty")
        if transcript_present is True and not phases:
            problems.append("transcript_present is true but phases is empty")
        if (
            transcript_present is True
            and isinstance(session_export, dict)
            and session_export.get("error") is True
        ):
            problems.append("transcript_present is true but session_export.error is true")
        if transcript_present is True:
            total = 0
            for entry in phases:
                if isinstance(entry, dict):
                    for key in ("token_count", "message_count"):
                        value = entry.get(key)
                        if is_non_negative_int(value):
                            total += value
            if total == 0:
                problems.append("transcript_present is true but all phase counts are zero")

    if problems:
        emit(False, "schema invalid: " + "; ".join(problems[:8]))
    else:
        emit(True, "")


if __name__ == "__main__":
    main()

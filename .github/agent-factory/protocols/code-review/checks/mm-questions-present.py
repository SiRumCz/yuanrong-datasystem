#!/usr/bin/env python3
"""Form check for the mental-model updater leg.

`questions` must be a well-formed list, and it MUST be non-empty when the agent
claims a mental-model change (`mm_changed: true`) — that single question is what
makes the human decide on the opened [mm] PR before mrp. Without this, an agent
that opens an MM PR but emits `questions: []` (or null/missing) would pass the
generic evidence-present check, and the engine's empty-data-gate auto-skip would
silently bypass the mandatory human decision. When `mm_changed` is false, an
empty list is correct (the gate then auto-completes straight to mrp).

ABI: mm-questions-present.py <evidence.json> <diff.txt> <changed-files.txt>
Prints one {"check","pass","feedback"} to stdout and always exits 0.
"""
import json
import sys


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        print(json.dumps({"check": "mm-questions-present", "pass": False,
                          "feedback": f"evidence unreadable / not JSON: {exc}"}))
        return

    if not isinstance(ev, dict):
        print(json.dumps({"check": "mm-questions-present", "pass": False,
                          "feedback": "evidence is not a JSON object"}))
        return

    questions = ev.get("questions")
    mm_changed = ev.get("mm_changed")
    problems = []

    if not isinstance(questions, list):
        problems.append("`questions` must be a (possibly empty) list")
    else:
        for q in questions:
            if not (isinstance(q, dict) and q.get("id") and q.get("text")):
                problems.append("each question needs a non-empty `id` and `text`")
                break

    if mm_changed is True and not (isinstance(questions, list) and len(questions) > 0):
        problems.append("mm_changed is true but `questions` is empty — emit exactly one "
                        "question so a human decides on the opened MM PR (else the gate is skipped)")

    if problems:
        print(json.dumps({"check": "mm-questions-present", "pass": False,
                          "feedback": "; ".join(problems[:4])}))
    else:
        print(json.dumps({"check": "mm-questions-present", "pass": True, "feedback": ""}))


if __name__ == "__main__":
    main()

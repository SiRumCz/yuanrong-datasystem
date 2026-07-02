#!/usr/bin/env python3
"""read-these-first-consistent (layer 3) — honest triage over the typed risk axes
+ spec cross-reference. Reads the bundled spec.md (sibling of evidence.json).
Usage: <ev.json> <diff> <changed-files>; exits 0."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402

HIGH_RISK = 2  # risk >= 2 must be surfaced


def emit(ok, feedback):
    print(json.dumps({"check": "read-these-first-consistent", "pass": ok, "feedback": feedback}))


def main():
    ev_path = sys.argv[1] if len(sys.argv) > 1 else ""
    ev = _common.load_evidence(ev_path)
    ledger = ev.get("ledger")
    rtf = ev.get("read_these_first")
    if not isinstance(ledger, list) or not ledger:
        emit(False, "ledger missing or empty")
        return
    if not isinstance(rtf, list):
        emit(False, "read_these_first missing or not a list")
        return
    by_id = {it.get("id"): it for it in ledger if isinstance(it, dict)}
    problems = []

    # every rtf entry references a real id
    for rid in rtf:
        if rid not in by_id:
            problems.append(f"read_these_first id {rid!r} not in ledger")

    # every high-risk item must be surfaced
    for it in ledger:
        if not isinstance(it, dict):
            continue
        if _common.RISK(it) >= HIGH_RISK and it.get("id") not in rtf:
            problems.append(f"high-risk item {it.get('id')!r} (risk "
                            f"{_common.RISK(it)}) buried — must be in read_these_first")

    # order monotonic non-increasing by risk (ties any order); only over known ids
    known = [r for r in rtf if r in by_id]
    risks = [_common.RISK(by_id[r]) for r in known]
    if any(risks[i] < risks[i + 1] for i in range(len(risks) - 1)):
        problems.append(f"read_these_first order is not risk-descending: {list(zip(known, risks))}")

    # cross-reference: every ledger id + its `what` must appear in the spec prose
    spec_path = _common.sibling(ev_path, "spec.md")
    if not spec_path:
        problems.append("bundled spec.md not found beside evidence.json (cannot cross-reference)")
    else:
        try:
            spec = open(spec_path, encoding="utf-8", errors="replace").read()
        except OSError as e:
            problems.append(f"unable to read spec.md: {e}")
            spec = ""
        # Anti-divergence anchor: every ledger id must appear in the spec's Ledger
        # section, so the human-read prose enumerates the same items as the JSON.
        # We deliberately DON'T require the `what` text to match verbatim — the spec
        # prose legitimately paraphrases the concise JSON `what`, and demanding an
        # exact substring is brittle (it fights the model's natural prose and buys
        # no real guarantee; semantic divergence is the substance boundary, §8.3).
        for it in ledger:
            if not isinstance(it, dict):
                continue
            i = it.get("id", "")
            if i and i not in spec:
                problems.append(f"ledger id {i!r} absent from spec.md (JSON/prose divergence)")

    if problems:
        emit(False, "; ".join(problems[:8]))
    else:
        emit(True, "")


if __name__ == "__main__":
    main()

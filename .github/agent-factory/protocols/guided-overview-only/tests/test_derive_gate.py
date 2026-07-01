#!/usr/bin/env python3
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "publish")
)
import _derive_gate as dg  # noqa: E402

failures = []


def check(n, got, want):
    if got != want:
        failures.append(f"{n}: got {got!r} want {want!r}")


# Custody golden values from reviewers/shape.js deriveGate.
check(
    "no present -> incomplete",
    dg.derive_gate({"present": [], "by_severity": {"critical": 3}})["verdict"],
    "incomplete",
)
check(
    "critical -> request-changes",
    dg.derive_gate({"present": ["correctness"], "by_severity": {"critical": 1}})[
        "verdict"
    ],
    "request-changes",
)
check(
    "high -> request-changes",
    dg.derive_gate({"present": ["test"], "by_severity": {"high": 2}})[
        "verdict"
    ],
    "request-changes",
)
check(
    "medium only -> warn",
    dg.derive_gate({"present": ["test"], "by_severity": {"medium": 1}})[
        "verdict"
    ],
    "warn",
)
check(
    "present, zero -> pass",
    dg.derive_gate({"present": ["test"], "by_severity": {}})["verdict"],
    "pass",
)
check(
    "counts normalized",
    dg.derive_gate({"present": ["x"], "by_severity": {"high": 2}})["counts"],
    {"critical": 0, "high": 2, "medium": 0, "low": 0},
)

if failures:
    print("FAIL test_derive_gate:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - _derive_gate matches custody shape.js")

#!/usr/bin/env python3
"""Unit test for checks/_diff.py parse_diff RIGHT/LEFT line maps."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "checks"))
import _diff  # noqa: E402

DIFF = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 ctx
-old
+new1
+new2
"""

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}: got {got!r} want {want!r}")


d = tempfile.mktemp()
open(d, "w").write(DIFF)
m = _diff.parse_diff(d)
right = m["foo.py"]["RIGHT"]
left = m["foo.py"]["LEFT"]
check("right has new1 at 2", right[2][0], "new1")
check("right has new2 at 3", right[3][0], "new2")
check("left has old at 2", left[2][0], "old")
check("ctx in both", (1 in right and 1 in left), True)

# Deleted file: +++ /dev/null => keyed under old path in LEFT map
DELETED_DIFF = """diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-line1
-line2
"""
dd = tempfile.mktemp()
open(dd, "w").write(DELETED_DIFF)
dm = _diff.parse_diff(dd)
check("deleted file keyed under old path", "gone.py" in dm, True)
check("deleted file LEFT has line1", dm["gone.py"]["LEFT"][1][0], "line1")

# 2-hunk single-file diff: lines in different hunks get different hunk_ids
TWO_HUNK_DIFF = """diff --git a/multi.py b/multi.py
--- a/multi.py
+++ b/multi.py
@@ -1,1 +1,2 @@
 ctx1
+added1
@@ -10,1 +10,2 @@
 ctx3
+added2
"""
td = tempfile.mktemp()
open(td, "w").write(TWO_HUNK_DIFF)
tm = _diff.parse_diff(td)
tr = tm["multi.py"]["RIGHT"]
# added1 is in hunk 0 (line 2), added2 is in hunk 1 (line 11)
check("hunk1 line present", 2 in tr, True)
check("hunk2 line present", 11 in tr, True)
check(
    "two hunks have different hunk_ids",
    tr[2][1] != tr[11][1],
    True,
)

if failures:
    print("FAIL test_diff:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - _diff.parse_diff RIGHT/LEFT maps")

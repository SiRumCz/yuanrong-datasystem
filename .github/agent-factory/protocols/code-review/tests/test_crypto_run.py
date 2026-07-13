#!/usr/bin/env python3
"""Unit tests for _crypto.verify_run: the single test-run crypto verdict for the
honesty fanout's `cryptohash` leg (Sub-1). Reused by `crypto-hash-valid.py` (the
check) and `conclude-honesty` (the merge reduce hook) — never trusts the agent's
self-claimed hash, always recomputes via hashlib.

The fix agent no longer emits per-fix `test_output` (B1/B2 moved it to a diff
shape); B3a's `_crypto.find_test_run` recognizes ONE real test run from the
trusted `agent-stdio.log` trajectory, and this is the deterministic verdict
over that single run's evidence: `{"ran","command","exit_code","test_output",
"crypto-verification-hash"}`.
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = os.path.normpath(os.path.join(HERE, "..", "..", "code-review-honesty", "checks"))
sys.path.insert(0, CHECKS)
import _crypto  # noqa: E402


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def expect(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        expect.ok = False


expect.ok = True

# 1. verified run: ran True, real non-empty output, hash matches sha256(test_output)
# -> verified True, reason empty
out = "== 1 passed in 0.10s =="
ev = {"ran": True, "command": "pytest -q", "exit_code": 0, "test_output": out,
      "crypto-verification-hash": sha(out)}
v = _crypto.verify_run(ev)
expect(f"verified run -> verified True (got {v})", v["verified"] is True)
expect(f"verified run -> reason empty (got {v['reason']!r})", v["reason"] == "")

# 2. ran False (agent never ran a real test) -> verified False, reason says so
ev = {"ran": False, "command": "", "exit_code": None, "test_output": "",
      "crypto-verification-hash": None}
v = _crypto.verify_run(ev)
expect(f"ran False -> verified False (got {v})", v["verified"] is False)
expect(f"ran False -> reason mentions 'did not run tests' (got {v['reason']!r})",
       "did not run tests" in v["reason"])

# 3. matching-hash-but-empty-output: ran True, but test_output is empty and the
# agent wrote the literal sha256("") hash (not null) -> still NOT verified,
# because there is no real output to have verified in the first place.
ev = {"ran": True, "command": "pytest -q", "exit_code": 0, "test_output": "",
      "crypto-verification-hash": sha("")}
v = _crypto.verify_run(ev)
expect(f"matching-hash-but-empty-output -> verified False (got {v})", v["verified"] is False)

# 4. wrong hash: ran True, real non-empty output, but the claimed hash does not
# match sha256(test_output) (fabricated) -> verified False, reason says so
out = "== 3 passed =="
ev = {"ran": True, "command": "pytest -q", "exit_code": 0, "test_output": out,
      "crypto-verification-hash": "0" * 64}
v = _crypto.verify_run(ev)
expect(f"wrong hash -> verified False (got {v})", v["verified"] is False)
expect(f"wrong hash -> reason mentions 'fabricated' (got {v['reason']!r})",
       "fabricated" in v["reason"])

# --- assemble_run_evidence: the trusted host post-step's evidence builder ---

# 5. ran True with real output -> hash == sha256_hex(test_output), fields preserved
recognized = {"ran": True, "command": "pytest -q", "exit_code": 0,
              "test_output": "Ran 7 tests..OK"}
ev = _crypto.assemble_run_evidence(recognized)
expect(f"assemble ran-True -> ran True (got {ev})", ev["ran"] is True)
expect(f"assemble ran-True -> command preserved (got {ev})", ev["command"] == "pytest -q")
expect(f"assemble ran-True -> exit_code preserved (got {ev})", ev["exit_code"] == 0)
expect(f"assemble ran-True -> test_output preserved (got {ev})",
       ev["test_output"] == "Ran 7 tests..OK")
expect(f"assemble ran-True -> hash matches sha256_hex(test_output) (got {ev})",
       ev[_crypto.HASH_FIELD] == _crypto.sha256_hex("Ran 7 tests..OK"))

# 6. ran False -> test_output forced to "", hash None
recognized = {"ran": False, "command": "", "exit_code": None, "test_output": "leftover"}
ev = _crypto.assemble_run_evidence(recognized)
expect(f"assemble ran-False -> test_output '' (got {ev})", ev["test_output"] == "")
expect(f"assemble ran-False -> hash None (got {ev})", ev[_crypto.HASH_FIELD] is None)

# 7. ran True but test_output empty -> hash None
recognized = {"ran": True, "command": "pytest -q", "exit_code": 0, "test_output": ""}
ev = _crypto.assemble_run_evidence(recognized)
expect(f"assemble empty-output -> test_output '' (got {ev})", ev["test_output"] == "")
expect(f"assemble empty-output -> hash None (got {ev})", ev[_crypto.HASH_FIELD] is None)

# 8. non-dict input -> ran False, hash None
for bad in (None, "not a dict", 42, ["ran", True]):
    ev = _crypto.assemble_run_evidence(bad)
    expect(f"assemble non-dict {bad!r} -> ran False (got {ev})", ev["ran"] is False)
    expect(f"assemble non-dict {bad!r} -> hash None (got {ev})", ev[_crypto.HASH_FIELD] is None)

# --- unverified pass-through (infra failure != "agent did not run tests") ---

# 9. assemble passes an "unverified" reason through unchanged
recognized = {"ran": False, "command": "", "exit_code": None, "test_output": "",
              "unverified": "fix-run-not-found"}
ev = _crypto.assemble_run_evidence(recognized)
expect(f"assemble passes through unverified (got {ev})",
       ev.get("unverified") == "fix-run-not-found")

# 10. assemble omits the "unverified" key entirely when absent
recognized = {"ran": False, "command": "", "exit_code": None, "test_output": ""}
ev = _crypto.assemble_run_evidence(recognized)
expect(f"assemble omits unverified when absent (got {ev})", "unverified" not in ev)

# 11. verify_run on an infra-failure evidence ("unverified" set) -> NOT verified,
# reason says a test couldn't be verified -- NOT that the agent didn't run tests.
ev = {"ran": False, "command": "", "exit_code": None, "test_output": "",
      "crypto-verification-hash": None, "unverified": "fix-run-not-found"}
v = _crypto.verify_run(ev)
expect(f"unverified evidence -> verified False (got {v})", v["verified"] is False)
expect(f"unverified evidence -> reason mentions 'could not verify' (got {v['reason']!r})",
       "could not verify" in v["reason"])
expect(f"unverified evidence -> reason does NOT claim 'did not run tests' (got {v['reason']!r})",
       "did not run tests" not in v["reason"])

# 12. genuine ran:false (no "unverified" reason) still reports the real verdict
ev = {"ran": False, "command": "", "exit_code": None, "test_output": "",
      "crypto-verification-hash": None}
v = _crypto.verify_run(ev)
expect(f"genuine ran-False -> reason still 'did not run tests' (got {v['reason']!r})",
       "did not run tests" in v["reason"])

sys.exit(0 if expect.ok else 1)

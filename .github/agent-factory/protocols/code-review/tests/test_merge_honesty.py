#!/usr/bin/env python3
"""Regression: the honesty `merge` verdict reads its two PARALLEL fanout legs' evidence.

`cryptohash` and `fixverify` are branches of the `honesty` fanout (not linear
phases): `... -> fix -> honesty{cryptohash ‖ fixverify} -> join-honesty ->
honesty-verdict(merge)`. The engine does not deliver `inputs` to static fanout
legs, so `cryptohash` self-fetches the fix run's evidence via `gh run download`
instead of relying on input delivery. The top merge reads `{from:cryptohash}` +
`{from:fixverify}` — resolved path-aware to each leg's evidence under the
`honesty` fanout (not the legacy phase case). This test drives the real
lib.run_merge_hook exactly as next.py does and asserts the AND-verdict for the real
Sub-1 shape: `cryptohash` carries the single recognized test-run evidence
(`{"ran","test_output","crypto-verification-hash"}` — a real sha256, recomputed
by conclude-honesty via `_crypto.verify_run`, never trusted as a self-claim)
and `fixverify` carries its {check,pass,reason}.
"""
import hashlib, json, os, sys, shutil, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "engine"))
PROTO = os.path.normpath(os.path.join(HERE, "..", "..", "code-review-honesty", "protocol.json"))
sys.path.insert(0, ENGINE)
os.environ["ENGINE_LOCAL"] = "1"
import lib  # noqa: E402

proto = json.load(open(PROTO))
merge = next(s for s in proto["states"] if s.get("kind") == "merge")
PID = proto["name"]
INST = "pr-1"


def verdict(test_output, fixverify_pass):
    """Persist the two honesty phases' evidence where the engine writes them, then
    run the merge hook exactly as next.py does (consuming_path = the merge's node path).

    cryptohash phase carries the real single-run crypto shape: `ran` (True iff a
    test actually ran), `test_output`, and its real sha256
    `crypto-verification-hash` (hashlib, not a fixture constant) — or `null` when
    `test_output` is empty, mirroring an unverified run that conclude-honesty
    must catch."""
    d = tempfile.mkdtemp(prefix="merge-honesty-test-")
    try:
        ran = bool(test_output)
        crypto_hash = hashlib.sha256(test_output.encode("utf-8")).hexdigest() if test_output else None
        cryptohash_ev = {"ran": ran, "command": "pytest -q", "exit_code": 0 if ran else None,
                          "test_output": test_output, "crypto-verification-hash": crypto_hash}
        fixverify_ev = {"check": "fixverify", "pass": fixverify_pass,
                         "reason": "present" if fixverify_pass else "bug remains"}
        for leg, ev in (("cryptohash", cryptohash_ev), ("fixverify", fixverify_ev)):
            wp = lib.output_artifact_path(d, PID, INST, path=lib.state_path(proto, ["honesty", leg]))
            os.makedirs(os.path.dirname(wp), exist_ok=True)
            with open(wp, "w") as f:
                json.dump(ev, f)
        return lib.run_merge_hook(d, PID, INST, PROTO, merge, consuming_path=[merge["id"]])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def expect(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        expect.ok = False


expect.ok = True

r = verdict("== 1 passed ==", True)
expect(f"both legs pass (real hash + fixverify) -> success (got {r.get('conclusion')})",
       r.get("conclusion") == "success")
r = verdict("", True)
expect(f"cryptohash: agent didn't run tests (ran False) -> failure (got {r.get('conclusion')})",
       r.get("conclusion") == "failure")
r = verdict("== 1 passed ==", False)
expect(f"fixverify fail -> failure (got {r.get('conclusion')})", r.get("conclusion") == "failure")

sys.exit(0 if expect.ok else 1)

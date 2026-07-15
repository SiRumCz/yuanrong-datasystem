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

Also covers the E-9 vacuity case: `triage` is a linear phase (a top-level sibling
of the `honesty` fanout, not one of its legs), so the merge's `{from: triage}`
input resolves via the plain phase-id case to `triage.evidence.json`. When triage
found zero clusters, conclude-honesty must short-circuit to a `success` "nothing
to verify" verdict BEFORE evaluating the cryptohash/fixverify legs at all — even
if those legs are shaped like a failure. Conversely, when triage found clusters,
vacuity must NOT kick in: a lazy fix agent must still be caught (unchanged
failure behavior) — vacuity is keyed off triage's emptiness, never the fix
agent's own (possibly also empty) output.
"""
import hashlib, json, os, sys, shutil, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "engine"))
PROTO = os.path.normpath(os.path.join(HERE, "..", "protocol.json"))
sys.path.insert(0, ENGINE)
os.environ["ENGINE_LOCAL"] = "1"
import lib  # noqa: E402

proto = json.load(open(PROTO))
# The honesty verdict is now a NESTED merge inside the per-issue fanout's `each`
# sub-pipeline (folded graph). Locate it under per-issue.each.states, and drive it
# on one representative per-issue leg (<lid>) so its {from:cryptohash/fixverify/triage}
# inputs resolve path-aware to THIS leg's own evidence.
_per_issue = next(s for s in proto["states"] if s.get("id") == "per-issue")
_each_states = _per_issue["each"]["states"]
merge = next(s for s in _each_states if s.get("kind") == "merge")
PID = proto["name"]
INST = "pr-1"
LID = hashlib.sha1(b"1").hexdigest()[:8]        # a representative per-issue leg id


def verdict(test_output, fixverify_pass, triage=None):
    """Persist the two honesty phases' evidence where the engine writes them, then
    run the merge hook exactly as next.py does (consuming_path = the merge's node path).

    cryptohash phase carries the real single-run crypto shape: `ran` (True iff a
    test actually ran), `test_output`, and its real sha256
    `crypto-verification-hash` (hashlib, not a fixture constant) — or `null` when
    `test_output` is empty, mirroring an unverified run that conclude-honesty
    must catch.

    `triage` (default None) optionally persists the upstream triage PHASE's
    evidence (a linear sibling of the `honesty` fanout, not one of its legs) at
    `triage.evidence.json` — the same phase-id file the merge's `{from: triage}`
    input resolves to. Left unwritten by default so the pre-E-9 regression cases
    exercise the missing-input (fail-safe, unchanged-behavior) path."""
    d = tempfile.mkdtemp(prefix="merge-honesty-test-")
    try:
        ran = bool(test_output)
        crypto_hash = hashlib.sha256(test_output.encode("utf-8")).hexdigest() if test_output else None
        cryptohash_ev = {"ran": ran, "command": "pytest -q", "exit_code": 0 if ran else None,
                          "test_output": test_output, "crypto-verification-hash": crypto_hash}
        fixverify_ev = {"check": "fixverify", "pass": fixverify_pass,
                         "reason": "present" if fixverify_pass else "bug remains"}
        for leg, ev in (("cryptohash", cryptohash_ev), ("fixverify", fixverify_ev)):
            wp = lib.output_artifact_path(d, PID, INST, path=lib.state_path(proto, ["per-issue", LID, "honesty", leg]))
            os.makedirs(os.path.dirname(wp), exist_ok=True)
            with open(wp, "w") as f:
                json.dump(ev, f)
        if triage is not None:
            tp = lib.output_artifact_path(d, PID, INST, path=lib.state_path(proto, ["per-issue", LID, "triage"]))
            os.makedirs(os.path.dirname(tp), exist_ok=True)
            with open(tp, "w") as f:
                json.dump(triage, f)
        return lib.run_merge_hook(d, PID, INST, PROTO, merge, consuming_path=["per-issue", LID, merge["id"]])
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

# E-9: vacuity keyed off triage's clusters, never the fix agent's own output.
r = verdict("", False, triage={"clusters": [], "summary": "clean"})
expect(f"triage clusters=[] (both legs failing-shaped) -> success 'nothing to verify' "
       f"(got {r.get('conclusion')}/{r.get('summary')!r})",
       r.get("conclusion") == "success" and "nothing to verify" in r.get("summary", ""))

r = verdict("", True, triage={"clusters": [{"id": "c1"}], "summary": "1 cluster"})
expect(f"triage clusters=[1] + cryptohash didn't run -> unchanged failure 'did not run tests' "
       f"(got {r.get('conclusion')}/{r.get('summary')!r}) — lazy fix agent must still be caught",
       r.get("conclusion") == "failure" and "did not run tests" in r.get("summary", ""))

r = verdict("", False, triage={"clusters": None})
expect(f"triage malformed (clusters not a list) -> falls through to unchanged failure "
       f"(got {r.get('conclusion')})", r.get("conclusion") == "failure")

sys.exit(0 if expect.ok else 1)

#!/usr/bin/env python3
"""Regression: a TOP `merge` state must read its fanout legs' evidence path-aware.

Guards lib.run_merge_hook's top-merge input resolution. The code-review-honesty
demo puts its 2-branch honesty fanout SECOND (after `review`); a leg persists to
`honesty.<leg>.evidence.json`. The legacy top-merge resolver read `<leg>.evidence.json`
(no fanout prefix, assuming the FIRST fanout), so it never found the legs and every
run reported NOT-honest even when both legs passed. This test drives the real
lib.run_merge_hook exactly as next.py does and asserts the AND-verdict is correct.
"""
import json, os, sys, shutil, tempfile

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


def verdict(testhash_pass, fixverify_pass):
    """Persist the two honesty legs where the engine writes them, then run the
    merge hook exactly as next.py does (consuming_path = the merge's node path)."""
    d = tempfile.mkdtemp(prefix="merge-honesty-test-")
    try:
        for leg, ok in (("testhash", testhash_pass), ("fixverify", fixverify_pass)):
            wp = lib.output_artifact_path(d, PID, INST, path=lib.state_path(proto, ["honesty", leg]))
            os.makedirs(os.path.dirname(wp), exist_ok=True)
            with open(wp, "w") as f:
                json.dump({"check": leg, "pass": ok, "reason": "ok" if ok else "bug remains"}, f)
        return lib.run_merge_hook(d, PID, INST, PROTO, merge, consuming_path=[merge["id"]])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def expect(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        expect.ok = False


expect.ok = True

r = verdict(True, True)
expect(f"both legs pass -> success (got {r.get('conclusion')})", r.get("conclusion") == "success")
r = verdict(True, False)
expect(f"fixverify fail -> failure (got {r.get('conclusion')})", r.get("conclusion") == "failure")
r = verdict(False, True)
expect(f"testhash fail  -> failure (got {r.get('conclusion')})", r.get("conclusion") == "failure")

sys.exit(0 if expect.ok else 1)

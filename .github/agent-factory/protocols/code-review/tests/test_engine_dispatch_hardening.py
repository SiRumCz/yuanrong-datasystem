#!/usr/bin/env python3
"""Engine audit E-1/E-3 hardening (golivax rotation lineage).

E-1: lib.finalize_merge_result maps a merge result to the (conclusion, summary,
label) the TOP-merge finalize arm publishes. A crashed / unresolved / no-verdict
hook (conclusion 'neutral', None, or malformed) must GATE (failure + 'failed');
a genuine success/failure verdict passes through with 'done'.

E-3 (added in the dispatch task): lib._gh_dispatch / advance.gh_api delegate to
lib.run_gh_rotating and fail LOUD when rotation can't land the call.
"""
import contextlib, importlib.util, io, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "engine"))
ADVANCE = os.path.join(ENGINE, "advance.py")
sys.path.insert(0, ENGINE)
import lib  # noqa: E402
spec = importlib.util.spec_from_file_location("advance", ADVANCE)
advance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advance)

def expect(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        expect.ok = False
expect.ok = True

# --- E-1 lib.finalize_merge_result ---
r = lib.finalize_merge_result({"conclusion": "success", "summary": "all checks passed"})
expect(f"success verdict passes through (got {r})", r == ("success", "all checks passed", "done"))
r = lib.finalize_merge_result({"conclusion": "failure", "summary": "NOT honest: fabricated evidence"})
expect(f"genuine failure passes through (got {r})", r == ("failure", "NOT honest: fabricated evidence", "done"))
r = lib.finalize_merge_result({"conclusion": "neutral", "summary": "merge hook failed"})
expect(f"neutral gates as failure/failed (got {r})", r[0] == "failure" and "gate blocked" in r[1] and r[2] == "failed")
r = lib.finalize_merge_result(None)
expect(f"None gates as failure/failed (got {r})", r[0] == "failure" and r[2] == "failed")
r = lib.finalize_merge_result("not-a-dict")
expect(f"non-dict gates as failure/failed (got {r})", r[0] == "failure" and r[2] == "failed")
r = lib.finalize_merge_result({"summary": "no conclusion key"})
expect(f"missing conclusion gates as failure/failed (got {r})", r[0] == "failure" and r[2] == "failed")

# --- E-3 dispatch loud-fail on the rotation lineage (stub lib.run_gh_rotating) ---
class _FakeResult:
    def __init__(self, returncode, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout

calls = []
def _rot_fail(*_a, **_k):
    calls.append(1); return _FakeResult(1, "403 rate limited")
def _rot_ok(*_a, **_k):
    calls.append(1); return _FakeResult(0, "", "{}")

_orig_rot = lib.run_gh_rotating
_orig_engine_local = os.environ.get("ENGINE_LOCAL")
os.environ.pop("ENGINE_LOCAL", None)
try:
    # _gh_dispatch raises RuntimeError naming a replay when rotation cannot land it
    calls.clear(); lib.run_gh_rotating = _rot_fail
    raised = False; err_msg = ""
    try:
        lib._gh_dispatch("protocol-continue", {"protocol": "p", "instance": "i"})
    except RuntimeError as e:
        raised = True; err_msg = str(e)
    expect(f"_gh_dispatch raises RuntimeError on rotation failure (raised={raised})", raised)
    expect(f"_gh_dispatch names replay + event_type (msg={err_msg!r})",
           "recover by re-firing" in err_msg and "protocol-continue" in err_msg)

    calls.clear(); lib.run_gh_rotating = _rot_ok
    raised = False
    try:
        lib._gh_dispatch("protocol-continue", {"protocol": "p", "instance": "i"})
    except RuntimeError:
        raised = True
    expect(f"_gh_dispatch succeeds without raising (raised={raised})", not raised)
    expect(f"_gh_dispatch calls rotation once on success (got {len(calls)})", len(calls) == 1)

    calls.clear(); lib.run_gh_rotating = _rot_fail
    os.environ["ENGINE_LOCAL"] = "1"
    lib._gh_dispatch("protocol-continue", {"protocol": "p", "instance": "i"})
    expect(f"ENGINE_LOCAL short-circuits _gh_dispatch (got {len(calls)})", len(calls) == 0)
    os.environ.pop("ENGINE_LOCAL", None)

    # advance.gh_api sys.exit(1)s with a replay when rotation cannot land it
    calls.clear(); lib.run_gh_rotating = _rot_fail
    exited = None; buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            advance.gh_api("some/path")
    except SystemExit as e:
        exited = e.code
    out = buf.getvalue()
    expect(f"gh_api sys.exit(1)s on rotation failure (exit {exited})", exited == 1)
    expect(f"gh_api stderr carries replay (stderr={out!r})",
           "recover by re-firing" in out and "some/path" in out)

    calls.clear(); lib.run_gh_rotating = _rot_ok
    exited = None
    try:
        advance.gh_api("some/path")
    except SystemExit as e:
        exited = e.code
    expect(f"gh_api does not exit on success (exit {exited})", exited is None)

    calls.clear(); lib.run_gh_rotating = _rot_fail
    os.environ["ENGINE_LOCAL"] = "1"
    advance.gh_api("some/path")
    expect(f"ENGINE_LOCAL short-circuits gh_api (got {len(calls)})", len(calls) == 0)
finally:
    lib.run_gh_rotating = _orig_rot
    if _orig_engine_local is None:
        os.environ.pop("ENGINE_LOCAL", None)
    else:
        os.environ["ENGINE_LOCAL"] = _orig_engine_local

sys.exit(0 if expect.ok else 1)

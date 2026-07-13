#!/usr/bin/env python3
"""Engine audit E-1/E-3 hardening.

E-1: run_merge_hook falls back to {"conclusion": "neutral", ...} whenever the
verdict hook is unresolved, crashes, or returns malformed stdout. The TOP-merge
finalize arm in next.py used to publish that neutral verbatim to the final
check-run and label the instance "done" — a CRASHED verdict hook looked
finished. lib.finalize_merge_result maps a merge result to the
(conclusion, summary, label) next.py actually publishes: neutral must GATE
(failure + 'failed'), a genuine success/failure verdict passes through with
'done'.

E-3: every engine dispatch hop (lib._gh_dispatch, advance.gh_api) ran 'gh api
...' fire-and-forget or logged-and-continued on failure. A 403 (shared PAT
rate limit) or 5xx silently dropped the next hop while state was already
advanced -> silent stall. Both now retry up to 3 attempts (5s apart) and then
fail LOUD: _gh_dispatch raises RuntimeError (its callers run inside next.py,
which should crash the job); gh_api calls sys.exit(1) (advance.py is a script).
"""
import contextlib
import importlib.util
import io
import os
import sys
import time

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

# --- lib.finalize_merge_result ----------------------------------------------

r = lib.finalize_merge_result({"conclusion": "success", "summary": "all checks passed"})
expect(f"success verdict passes through (got {r})",
       r == ("success", "all checks passed", "done"))

r = lib.finalize_merge_result({"conclusion": "failure", "summary": "NOT honest: fabricated evidence"})
expect(f"genuine failure verdict passes through (got {r})",
       r == ("failure", "NOT honest: fabricated evidence", "done"))

r = lib.finalize_merge_result({"conclusion": "neutral", "summary": "merge hook failed"})
expect(f"neutral gates as failure/failed (got {r})",
       r[0] == "failure" and "gate blocked" in r[1] and r[2] == "failed")

r = lib.finalize_merge_result(None)
expect(f"None result gates as failure/failed (got {r})", r[0] == "failure" and r[2] == "failed")

r = lib.finalize_merge_result("not-a-dict")
expect(f"non-dict result gates as failure/failed (got {r})", r[0] == "failure" and r[2] == "failed")

r = lib.finalize_merge_result({"summary": "no conclusion key at all"})
expect(f"missing conclusion key gates as failure/failed (got {r})",
       r[0] == "failure" and r[2] == "failed")

# --- shared subprocess.run stub (lib.subprocess and advance.subprocess are the
# same module object, so patching either name mutates the same `run` attribute) ---


class _FakeResult:
    def __init__(self, returncode, stderr):
        self.returncode = returncode
        self.stderr = stderr


calls = []


def _fail(*_args, **_kwargs):
    calls.append(1)
    return _FakeResult(1, "403 rate limited")


def _ok(*_args, **_kwargs):
    calls.append(1)
    return _FakeResult(0, "")


_orig_run = lib.subprocess.run
_orig_sleep = time.sleep
time.sleep = lambda *_a, **_k: None  # _gh_dispatch/gh_api do a local `import time`
_orig_engine_local = os.environ.get("ENGINE_LOCAL")
os.environ.pop("ENGINE_LOCAL", None)

try:
    # --- lib._gh_dispatch loud failure ---
    calls.clear()
    lib.subprocess.run = _fail
    raised = False
    err_msg = ""
    try:
        lib._gh_dispatch("protocol-continue", {"protocol": "p", "instance": "i"})
    except RuntimeError as e:
        raised = True
        err_msg = str(e)
    expect(f"_gh_dispatch raises RuntimeError after retries (raised={raised})", raised)
    expect(f"_gh_dispatch retried 3 times before raising (got {len(calls)})", len(calls) == 3)
    expect(f"_gh_dispatch error names event_type and replay command (msg={err_msg!r})",
           "recover by re-firing" in err_msg and "protocol-continue" in err_msg)

    calls.clear()
    lib.subprocess.run = _ok
    raised = False
    try:
        lib._gh_dispatch("protocol-continue", {"protocol": "p", "instance": "i"})
    except RuntimeError:
        raised = True
    expect(f"_gh_dispatch succeeds without raising (raised={raised})", not raised)
    expect(f"_gh_dispatch called once on success (got {len(calls)})", len(calls) == 1)

    calls.clear()
    lib.subprocess.run = _fail
    os.environ["ENGINE_LOCAL"] = "1"
    lib._gh_dispatch("protocol-continue", {"protocol": "p", "instance": "i"})
    expect(f"ENGINE_LOCAL short-circuits _gh_dispatch, gh api never called (got {len(calls)})",
           len(calls) == 0)
    os.environ.pop("ENGINE_LOCAL", None)

    # --- advance.gh_api loud failure ---
    calls.clear()
    advance.subprocess.run = _fail
    exited = None
    stderr_buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr_buf):
            advance.gh_api("some/path")
    except SystemExit as e:
        exited = e.code
    stderr_out = stderr_buf.getvalue()
    expect(f"gh_api sys.exit(1)s after retries (exit code {exited})", exited == 1)
    expect(f"gh_api retried 3 times before exiting (got {len(calls)})", len(calls) == 3)
    expect(f"gh_api stderr carries replay command (stderr={stderr_out!r})",
           "recover by re-firing" in stderr_out and "some/path" in stderr_out)

    calls.clear()
    advance.subprocess.run = _ok
    exited = None
    try:
        advance.gh_api("some/path")
    except SystemExit as e:
        exited = e.code
    expect(f"gh_api does not exit on success (exit code {exited})", exited is None)
    expect(f"gh_api called once on success (got {len(calls)})", len(calls) == 1)

    calls.clear()
    advance.subprocess.run = _fail
    os.environ["ENGINE_LOCAL"] = "1"
    advance.gh_api("some/path")
    expect(f"ENGINE_LOCAL short-circuits gh_api, gh api never called (got {len(calls)})",
           len(calls) == 0)
finally:
    lib.subprocess.run = _orig_run
    time.sleep = _orig_sleep
    if _orig_engine_local is None:
        os.environ.pop("ENGINE_LOCAL", None)
    else:
        os.environ["ENGINE_LOCAL"] = _orig_engine_local

sys.exit(0 if expect.ok else 1)

#!/usr/bin/env python3
"""Unit tests for lib.run_gh_rotating — the engine's GitHub write-token pool.

The pool is PUBLISH_TOKEN, then PUBLISH_TOKEN_2 … PUBLISH_TOKEN_9 (each wired in
the workflow to a distinct dispatch-PAT secret, e.g. POC_DISPATCH_TOKEN /
POC_DISPATCH_TOKEN_2). On a 403/429 rate-limit it CYCLES to the next token and,
when a full lap finds all exhausted, waits GH_ROTATE_WAIT_S (bounded by
GH_ROTATE_MAX_WAIT_S) before lapping again.

Self-contained: stubs PyYAML, puts the engine dir on sys.path, and monkeypatches
lib.subprocess.run and lib.time.sleep — no real `gh`, network, or waiting.
Run: `python3 .github/agent-factory/engine/test_run_gh_rotating.py`
"""
import os
import sys
import types
import unittest

_ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ENGINE)
sys.modules.setdefault("yaml", types.ModuleType("yaml"))

import subprocess  # noqa: E402
import lib  # noqa: E402

_RATE_LIMIT = "gh: API rate limit exceeded for user ID 999. (HTTP 403)"
_TOKEN_ENVS = ["PUBLISH_TOKEN"] + [f"PUBLISH_TOKEN_{i}" for i in range(2, 10)]


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class RunGhRotatingTest(unittest.TestCase):
    def setUp(self):
        self._real_run = lib.subprocess.run
        self._real_sleep = lib.time.sleep
        self.calls = []
        self.sleeps = []
        lib.time.sleep = lambda s: self.sleeps.append(s)
        for k in _TOKEN_ENVS + ["GH_ROTATE_WAIT_S", "GH_ROTATE_MAX_WAIT_S"]:
            os.environ.pop(k, None)
        os.environ["GH_ROTATE_MAX_WAIT_S"] = "0"   # default: fail fast (no wait) for determinism

    def tearDown(self):
        lib.subprocess.run = self._real_run
        lib.time.sleep = self._real_sleep

    def _fake_by_token(self, behavior):
        def run(argv, text=None, capture_output=None, env=None, check=False):
            tok = (env or {}).get("GH_TOKEN", "")
            self.calls.append(tok)
            return behavior[tok] if isinstance(behavior, dict) else behavior(tok)
        lib.subprocess.run = run

    def _fake_sequence(self, results):
        seq = iter(results)
        def run(argv, text=None, capture_output=None, env=None, check=False):
            self.calls.append((env or {}).get("GH_TOKEN", ""))
            return next(seq)
        lib.subprocess.run = run

    # --- pool assembly -----------------------------------------------------
    def test_pool_order_is_primary_then_numbered(self):
        os.environ["PUBLISH_TOKEN"] = "tA"
        os.environ["PUBLISH_TOKEN_2"] = "tB"
        os.environ["PUBLISH_TOKEN_3"] = "tC"
        self.assertEqual(lib._publish_tokens(), ["tA", "tB", "tC"])

    # --- single-lap forward rotation --------------------------------------
    def test_rotates_to_next_token_on_rate_limit(self):
        os.environ["PUBLISH_TOKEN"] = "t1"       # POC_DISPATCH_TOKEN
        os.environ["PUBLISH_TOKEN_2"] = "t2"      # POC_DISPATCH_TOKEN_2
        self._fake_by_token({"t1": _Result(1, "", _RATE_LIMIT), "t2": _Result(0, "ok")})
        r = lib.run_gh_rotating(["repos/x/y", "-f", "body=z"])
        self.assertEqual((r.returncode, r.stdout), (0, "ok"))
        self.assertEqual(self.calls, ["t1", "t2"])

    def test_permission_403_does_not_rotate(self):
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        self._fake_by_token({"t1": _Result(1, "", "gh: Must have admin rights (HTTP 403)"),
                             "t2": _Result(0, "ok")})
        r = lib.run_gh_rotating(["repos/x/y"])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls, ["t1"])

    def test_secondary_429_rotates(self):
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        self._fake_by_token({"t1": _Result(1, "", "secondary rate limit (HTTP 429)"),
                             "t2": _Result(0, "ok")})
        self.assertEqual(lib.run_gh_rotating(["repos/x/y"]).returncode, 0)
        self.assertEqual(self.calls, ["t1", "t2"])

    def test_single_token_no_rotation(self):
        os.environ["PUBLISH_TOKEN"] = "solo"
        self._fake_by_token(lambda tok: _Result(0, "ok"))
        lib.run_gh_rotating(["repos/x/y"])
        self.assertEqual(self.calls, ["solo"])

    def test_empty_pool_is_single_ambient_call(self):
        self._fake_by_token(lambda tok: _Result(0, "ok"))
        lib.run_gh_rotating(["repos/x/y"])
        self.assertEqual(self.calls, [""])

    def test_no_wait_budget_fails_after_one_lap(self):
        # GH_ROTATE_MAX_WAIT_S=0 (setUp default): both exhausted => one lap, then raise.
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        self._fake_by_token({"t1": _Result(1, "", _RATE_LIMIT), "t2": _Result(1, "", _RATE_LIMIT)})
        with self.assertRaises(subprocess.CalledProcessError):
            lib.run_gh_rotating(["repos/x/y"], check=True)
        self.assertEqual(self.calls, ["t1", "t2"])
        self.assertEqual(self.sleeps, [])  # no waiting when budget is 0

    # --- cycle back to the first token after a wait ------------------------
    def test_cycles_back_to_first_and_recovers_after_wait(self):
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        os.environ["GH_ROTATE_WAIT_S"] = "60"
        os.environ["GH_ROTATE_MAX_WAIT_S"] = "600"
        # t1 out, t2 out -> [wait] -> t1's window reset -> success on the lap back.
        self._fake_sequence([_Result(1, "", _RATE_LIMIT),   # t1
                             _Result(1, "", _RATE_LIMIT),   # t2
                             _Result(0, "ok")])             # back to t1, recovered
        r = lib.run_gh_rotating(["repos/x/y"])
        self.assertEqual((r.returncode, r.stdout), (0, "ok"))
        self.assertEqual(self.calls, ["t1", "t2", "t1"])   # cycled back to the first
        self.assertEqual(self.sleeps, [60])                # waited once between laps

    def test_wait_is_bounded_then_gives_up(self):
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        os.environ["GH_ROTATE_WAIT_S"] = "60"
        os.environ["GH_ROTATE_MAX_WAIT_S"] = "120"   # -> at most two 60s waits
        self._fake_by_token(lambda tok: _Result(1, "", _RATE_LIMIT))  # everything always out
        with self.assertRaises(subprocess.CalledProcessError):
            lib.run_gh_rotating(["repos/x/y"], check=True)
        self.assertEqual(self.sleeps, [60, 60])            # bounded: exactly two waits
        self.assertEqual(self.calls, ["t1", "t2"] * 3)     # three laps, then stop


if __name__ == "__main__":
    unittest.main(verbosity=2)

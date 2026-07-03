#!/usr/bin/env python3
"""Unit tests for lib.run_gh_rotating — the engine's GitHub write-token pool.

Self-contained: stubs PyYAML and puts the engine dir on sys.path, so it runs
from a bare checkout with `python3 .github/agent-factory/engine/test_run_gh_rotating.py`
(or under pytest/unittest). Monkeypatches lib.subprocess.run so no real `gh` /
network is touched.
"""
import os
import sys
import types
import unittest

_ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ENGINE)
sys.modules.setdefault("yaml", types.ModuleType("yaml"))  # lib imports yaml; unused here

import subprocess  # noqa: E402
import lib  # noqa: E402

_RATE_LIMIT = "gh: API rate limit exceeded for user ID 999. (HTTP 403)"


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class RunGhRotatingTest(unittest.TestCase):
    def setUp(self):
        self._real_run = lib.subprocess.run
        self.calls = []
        for k in ("PUBLISH_TOKENS", "PUBLISH_TOKEN"):
            os.environ.pop(k, None)

    def tearDown(self):
        lib.subprocess.run = self._real_run

    def _fake(self, behavior):
        def run(argv, text=None, capture_output=None, env=None, check=False):
            tok = (env or {}).get("GH_TOKEN", "")
            self.calls.append(tok)
            return behavior[tok] if isinstance(behavior, dict) else behavior(tok)
        lib.subprocess.run = run

    def test_rotates_to_next_token_on_rate_limit(self):
        os.environ["PUBLISH_TOKENS"] = "t1\nt2"
        self._fake({"t1": _Result(1, "", _RATE_LIMIT), "t2": _Result(0, "ok")})
        r = lib.run_gh_rotating(["repos/x/y", "-f", "body=z"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "ok")
        self.assertEqual(self.calls, ["t1", "t2"])

    def test_all_exhausted_with_check_raises(self):
        os.environ["PUBLISH_TOKENS"] = "t1\nt2"
        self._fake({"t1": _Result(1, "", _RATE_LIMIT), "t2": _Result(1, "", _RATE_LIMIT)})
        with self.assertRaises(subprocess.CalledProcessError):
            lib.run_gh_rotating(["repos/x/y"], check=True)
        self.assertEqual(self.calls, ["t1", "t2"])

    def test_permission_403_does_not_rotate(self):
        # A non-rate-limit 403 must NOT waste the next token.
        os.environ["PUBLISH_TOKENS"] = "t1\nt2"
        self._fake({"t1": _Result(1, "", "gh: Must have admin rights (HTTP 403)"),
                    "t2": _Result(0, "ok")})
        r = lib.run_gh_rotating(["repos/x/y"])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls, ["t1"])

    def test_secondary_429_rotates(self):
        os.environ["PUBLISH_TOKENS"] = "t1\nt2"
        self._fake({"t1": _Result(1, "", "You have exceeded a secondary rate limit (HTTP 429)"),
                    "t2": _Result(0, "ok")})
        r = lib.run_gh_rotating(["repos/x/y"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.calls, ["t1", "t2"])

    def test_empty_pool_is_single_ambient_call(self):
        # No pool configured => unchanged behavior (one call, inherit ambient GH_TOKEN).
        self._fake(lambda tok: _Result(0, "ok"))
        lib.run_gh_rotating(["repos/x/y"])
        self.assertEqual(self.calls, [""])

    def test_publish_token_fallback_when_no_pool(self):
        os.environ["PUBLISH_TOKEN"] = "solo"
        self._fake(lambda tok: _Result(0, "ok"))
        lib.run_gh_rotating(["repos/x/y"])
        self.assertEqual(self.calls, ["solo"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

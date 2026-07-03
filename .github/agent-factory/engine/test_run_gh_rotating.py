#!/usr/bin/env python3
"""Unit + integration tests for lib.run_gh_rotating — the engine's GitHub
write-token pool (PUBLISH_TOKEN, then PUBLISH_TOKEN_2 … PUBLISH_TOKEN_9).

Failover between tokens is always on. When a full lap finds every token
rate-limited it FAILS FAST by default (GH_ROTATE_MAX_WAIT_S=0); set that > 0 to
opt into cycling back to the first token with a bounded wait.

Self-contained: stubs PyYAML, puts the engine dir on sys.path, and monkeypatches
lib.subprocess.run / lib.time.sleep. One test uses a real fake-`gh` binary to
exercise the real subprocess + stderr classification. Run:
`python3 .github/agent-factory/engine/test_run_gh_rotating.py`
"""
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest

_ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ENGINE)
sys.modules.setdefault("yaml", types.ModuleType("yaml"))

import lib  # noqa: E402

_RATE_LIMIT = "gh: API rate limit exceeded for user ID 999. (HTTP 403)"
_TOKEN_ENVS = ["PUBLISH_TOKEN"] + [f"PUBLISH_TOKEN_{i}" for i in range(2, 10)]
_ROTATE_ENVS = ["GH_ROTATE_WAIT_S", "GH_ROTATE_MAX_WAIT_S"]


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
        for k in _TOKEN_ENVS + _ROTATE_ENVS + ["GITHUB_REPOSITORY", "ENGINE_LOCAL"]:
            os.environ.pop(k, None)
        # GH_ROTATE_MAX_WAIT_S is left UNSET so the production default (0 = fail
        # fast) is what these tests exercise. Cycling tests opt in explicitly.

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

    def test_publish_tokens_edge_cases(self):
        os.environ["PUBLISH_TOKEN"] = "  tA  "   # trimmed
        os.environ["PUBLISH_TOKEN_2"] = ""        # blank -> skipped
        os.environ["PUBLISH_TOKEN_3"] = "tC"      # gap: _2 unset but _3 present
        os.environ["PUBLISH_TOKEN_4"] = "tA"      # duplicate -> de-duped
        self.assertEqual(lib._publish_tokens(), ["tA", "tC"])

    def test_publish_tokens_all_unset(self):
        self.assertEqual(lib._publish_tokens(), [])

    # --- failover (always on) ---------------------------------------------
    def test_rotates_to_next_token_on_rate_limit(self):
        os.environ["PUBLISH_TOKEN"] = "t1"        # POC_DISPATCH_TOKEN
        os.environ["PUBLISH_TOKEN_2"] = "t2"       # POC_DISPATCH_TOKEN_2
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

    def test_abuse_detection_on_stdout_rotates(self):
        # legacy phrasing, surfaced on stdout (not stderr) — must still rotate.
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        self._fake_by_token({"t1": _Result(1, "You have triggered an abuse detection mechanism", ""),
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

    # --- default fail-fast + misconfig safety ------------------------------
    def test_default_is_fail_fast(self):
        # No GH_ROTATE_MAX_WAIT_S -> default 0 -> both out -> one lap, raise, no sleep.
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        self._fake_by_token(lambda tok: _Result(1, "", _RATE_LIMIT))
        with self.assertRaises(subprocess.CalledProcessError):
            lib.run_gh_rotating(["repos/x/y"], check=True)
        self.assertEqual(self.calls, ["t1", "t2"])
        self.assertEqual(self.sleeps, [])

    def test_bad_env_values_do_not_crash_or_hang(self):
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        os.environ["GH_ROTATE_WAIT_S"] = "abc"      # non-numeric -> default 60
        os.environ["GH_ROTATE_MAX_WAIT_S"] = "inf"   # non-finite -> default 0 (no hang)
        self._fake_by_token(lambda tok: _Result(1, "", _RATE_LIMIT))
        with self.assertRaises(subprocess.CalledProcessError):
            lib.run_gh_rotating(["repos/x/y"], check=True)
        self.assertEqual(self.sleeps, [])           # 'inf' did NOT create an unbounded wait
        self.assertEqual(self.calls, ["t1", "t2"])

    # --- opt-in cycle-back-with-wait --------------------------------------
    def test_cycles_back_to_first_and_recovers_after_wait(self):
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        os.environ["GH_ROTATE_WAIT_S"] = "60"
        os.environ["GH_ROTATE_MAX_WAIT_S"] = "600"   # opt in
        self._fake_sequence([_Result(1, "", _RATE_LIMIT),   # t1
                             _Result(1, "", _RATE_LIMIT),   # t2
                             _Result(0, "ok")])             # back to t1, recovered
        r = lib.run_gh_rotating(["repos/x/y"])
        self.assertEqual((r.returncode, r.stdout), (0, "ok"))
        self.assertEqual(self.calls, ["t1", "t2", "t1"])
        self.assertEqual(self.sleeps, [60])

    def test_wait_is_bounded_then_gives_up(self):
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        os.environ["GH_ROTATE_WAIT_S"] = "60"
        os.environ["GH_ROTATE_MAX_WAIT_S"] = "120"   # at most two 60s waits
        self._fake_by_token(lambda tok: _Result(1, "", _RATE_LIMIT))
        with self.assertRaises(subprocess.CalledProcessError):
            lib.run_gh_rotating(["repos/x/y"], check=True)
        self.assertEqual(self.sleeps, [60, 60])
        self.assertEqual(self.calls, ["t1", "t2"] * 3)

    # --- integration: a real writer routes through the helper --------------
    def test_writer_create_issue_rotates_end_to_end(self):
        # Not ENGINE_LOCAL: create_issue must actually call run_gh_rotating and rotate.
        os.environ["GITHUB_REPOSITORY"] = "o/r"
        os.environ["PUBLISH_TOKEN"] = "t1"
        os.environ["PUBLISH_TOKEN_2"] = "t2"
        self._fake_by_token({"t1": _Result(1, "", _RATE_LIMIT), "t2": _Result(0, "42\n")})
        self.assertEqual(lib.create_issue("title", "body"), "42")
        self.assertEqual(self.calls, ["t1", "t2"])

    # --- integration: real subprocess + real stderr classification --------
    def test_fake_gh_binary_real_subprocess(self):
        lib.subprocess.run = self._real_run  # use the REAL subprocess with a fake `gh`
        d = tempfile.mkdtemp()
        gh = os.path.join(d, "gh")
        with open(gh, "w") as fh:
            fh.write(
                "#!/bin/sh\n"
                'if [ "$GH_TOKEN" = "t1" ]; then\n'
                '  echo "gh: API rate limit exceeded for user ID 1. (HTTP 403)" 1>&2\n'
                "  exit 1\n"
                "fi\n"
                "echo ok\n")
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = d + os.pathsep + old_path
        try:
            os.environ["PUBLISH_TOKEN"] = "t1"
            os.environ["PUBLISH_TOKEN_2"] = "t2"
            r = lib.run_gh_rotating(["repos/x/y"])
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), "ok")
        finally:
            os.environ["PATH"] = old_path


if __name__ == "__main__":
    unittest.main(verbosity=2)

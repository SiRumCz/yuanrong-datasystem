# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unit tests for dscli status command."""

import subprocess
from unittest.mock import MagicMock, mock_open, patch

import pytest

from yr.datasystem.cli.status import Command


class TestListWorkers:
    """Tests for Command._list_workers()."""

    def test_no_workers_returns_empty_list(self):
        """pgrep exits non-zero (no match) → empty list."""
        cmd = Command()
        err = subprocess.CalledProcessError(1, ["pgrep"])
        with patch("subprocess.check_output", side_effect=err):
            result = cmd._list_workers()
        assert result == []

    def test_single_worker_returns_pid_and_address(self):
        """One pgrep line → one entry with pid and address."""
        cmd = Command()
        pgrep_output = "12345 datasystem_worker\n"
        cmdline_bytes = b"datasystem_worker\x00-worker_address=127.0.0.1:31501\x00"

        with patch("subprocess.check_output", return_value=pgrep_output):
            with patch("builtins.open", mock_open(read_data=cmdline_bytes)):
                result = cmd._list_workers()

        assert result == [{"pid": 12345, "address": "127.0.0.1:31501"}]

    def test_multiple_workers_returned(self):
        """Two pgrep lines → two entries."""
        cmd = Command()
        pgrep_output = "12345 datasystem_worker\n56789 datasystem_worker\n"
        cmdlines = {
            "/proc/12345/cmdline": b"datasystem_worker\x00-worker_address=127.0.0.1:31501\x00",
            "/proc/56789/cmdline": b"datasystem_worker\x00-worker_address=127.0.0.1:31502\x00",
        }

        def fake_open(path, *args, **kwargs):
            return mock_open(read_data=cmdlines[path])()

        with patch("subprocess.check_output", return_value=pgrep_output):
            with patch("builtins.open", side_effect=fake_open):
                result = cmd._list_workers()

        assert result == [
            {"pid": 12345, "address": "127.0.0.1:31501"},
            {"pid": 56789, "address": "127.0.0.1:31502"},
        ]

    def test_dscli_process_filtered_out(self):
        """pgrep line whose process name is 'dscli' is skipped."""
        cmd = Command()
        pgrep_output = "99999 dscli\n12345 datasystem_worker\n"
        cmdline_bytes = b"datasystem_worker\x00-worker_address=127.0.0.1:31501\x00"

        with patch("subprocess.check_output", return_value=pgrep_output):
            with patch("builtins.open", mock_open(read_data=cmdline_bytes)):
                result = cmd._list_workers()

        assert len(result) == 1
        assert result[0]["pid"] == 12345

    def test_cmdline_unreadable_pid_skipped_with_warning(self):
        """If /proc/<pid>/cmdline raises OSError, that PID is skipped."""
        cmd = Command()
        cmd.logger = MagicMock()
        pgrep_output = "12345 datasystem_worker\n"

        with patch("subprocess.check_output", return_value=pgrep_output):
            with patch("builtins.open", side_effect=OSError("permission denied")):
                result = cmd._list_workers()

        assert result == []
        cmd.logger.warning.assert_called_once()

    def test_cmdline_missing_worker_address_skipped_with_warning(self):
        """If cmdline has no -worker_address= token, PID is skipped."""
        cmd = Command()
        cmd.logger = MagicMock()
        pgrep_output = "12345 datasystem_worker\n"
        cmdline_bytes = b"datasystem_worker\x00-some_other_arg=value\x00"

        with patch("subprocess.check_output", return_value=pgrep_output):
            with patch("builtins.open", mock_open(read_data=cmdline_bytes)):
                result = cmd._list_workers()

        assert result == []
        cmd.logger.warning.assert_called_once()

    def test_pgrep_timeout_returns_failure(self):
        """subprocess.TimeoutExpired propagates as RuntimeError."""
        cmd = Command()
        with patch("subprocess.check_output",
                   side_effect=subprocess.TimeoutExpired(["pgrep"], 5)):
            with pytest.raises(RuntimeError, match="timed out"):
                cmd._list_workers()


class TestRun:
    """Tests for Command.run()."""

    def test_run_no_workers_exits_success(self):
        """run() returns SUCCESS when no workers found."""
        cmd = Command()
        with patch.object(cmd, "_list_workers", return_value=[]):
            result = cmd.run(MagicMock())
        assert result == Command.SUCCESS

    def test_run_with_workers_exits_success(self):
        """run() returns SUCCESS when workers are found."""
        cmd = Command()
        workers = [{"pid": 12345, "address": "127.0.0.1:31501"}]
        with patch.object(cmd, "_list_workers", return_value=workers):
            result = cmd.run(MagicMock())
        assert result == Command.SUCCESS

    def test_run_on_runtime_error_exits_failure(self):
        """run() returns FAILURE when _list_workers raises RuntimeError."""
        cmd = Command()
        with patch.object(cmd, "_list_workers", side_effect=RuntimeError("pgrep timed out")):
            result = cmd.run(MagicMock())
        assert result == Command.FAILURE

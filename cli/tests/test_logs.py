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
"""Unit tests for the ``dscli logs`` subcommand."""

import os
import unittest
from unittest import mock

from yr.datasystem.cli import logs


class TestLogsCommand(unittest.TestCase):
    """Tests for :class:`yr.datasystem.cli.logs.Command`."""

    @mock.patch.object(logs.Command, "load_worker_config_defaults")
    @mock.patch.object(
        logs.Command,
        "get_process_args",
        return_value=["/opt/yuanrong/datasystem/bin/datasystem_worker"],
    )
    @mock.patch("yr.datasystem.cli.logs.subprocess.run")
    @mock.patch.object(logs.Command, "find_worker_pid", return_value=4321)
    def test_run_prints_recent_lines_from_default_worker_info_log(
        self, _mock_pid, mock_run, _mock_args, mock_defaults
    ):
        """A running worker's log tail is printed and the command succeeds."""
        mock_defaults.return_value = {"log_dir": "./datasystem/logs", "log_filename": ""}
        mock_run.return_value = mock.Mock(returncode=0)

        cmd = logs.Command()
        args = mock.Mock(worker_address="127.0.0.1:31501", lines=100)
        rc = cmd.run(args)

        self.assertEqual(rc, logs.BaseCommand.SUCCESS)
        mock_run.assert_called_once_with(
            [
                "tail", "-n", "100",
                os.path.realpath("./datasystem/logs/datasystem_worker.INFO.log"),
            ],
        )

    @mock.patch.object(logs.Command, "load_worker_config_defaults")
    @mock.patch.object(
        logs.Command,
        "get_process_args",
        return_value=[
            "/opt/yuanrong/datasystem/bin/datasystem_worker",
            "--worker_address=127.0.0.1:31501",
            "--log_dir=/var/log/datasystem",
            "--log_filename=custom_worker",
        ],
    )
    @mock.patch("yr.datasystem.cli.logs.subprocess.run")
    @mock.patch.object(logs.Command, "find_worker_pid", return_value=4321)
    def test_run_uses_worker_cmdline_log_config(
        self, _mock_pid, mock_run, _mock_args, mock_defaults
    ):
        """Explicit worker log flags select the current INFO log file."""
        mock_defaults.return_value = {"log_dir": "./datasystem/logs", "log_filename": ""}
        mock_run.return_value = mock.Mock(returncode=0)

        cmd = logs.Command()
        args = mock.Mock(worker_address="127.0.0.1:31501", lines=100)
        rc = cmd.run(args)

        self.assertEqual(rc, logs.BaseCommand.SUCCESS)
        mock_run.assert_called_once_with(
            ["tail", "-n", "100", "/var/log/datasystem/custom_worker.INFO.log"],
        )

    @mock.patch("yr.datasystem.cli.logs.subprocess.run")
    def test_run_rejects_invalid_worker_address(self, mock_run):
        """A malformed worker address is rejected before running tail."""
        cmd = logs.Command()
        args = mock.Mock(worker_address="127.0.0.1:31501;touch /tmp/pwned", lines=100)
        rc = cmd.run(args)

        self.assertEqual(rc, logs.BaseCommand.FAILURE)
        mock_run.assert_not_called()

    @mock.patch("yr.datasystem.cli.logs.subprocess.run")
    def test_run_rejects_invalid_line_count(self, mock_run):
        """A malformed line count is rejected before running tail."""
        cmd = logs.Command()
        args = mock.Mock(worker_address="127.0.0.1:31501", lines="100;touch /tmp/pwned")
        rc = cmd.run(args)

        self.assertEqual(rc, logs.BaseCommand.FAILURE)
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

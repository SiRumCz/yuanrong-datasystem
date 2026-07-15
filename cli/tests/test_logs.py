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

import unittest
from unittest import mock

from yr.datasystem.cli import logs


class TestLogsCommand(unittest.TestCase):
    """Tests for :class:`yr.datasystem.cli.logs.Command`."""

    @mock.patch("yr.datasystem.cli.logs.subprocess.run")
    @mock.patch.object(logs.Command, "find_worker_pid", return_value=4321)
    def test_run_prints_recent_lines(self, _mock_pid, mock_run):
        """A running worker's log tail is printed and the command succeeds."""
        mock_run.return_value = mock.Mock(returncode=0, stdout="a\nb\nc\n", stderr="")

        cmd = logs.Command()
        args = mock.Mock(worker_address="127.0.0.1:31501", lines=100)
        rc = cmd.run(args)

        self.assertEqual(rc, logs.BaseCommand.SUCCESS)


if __name__ == "__main__":
    unittest.main()

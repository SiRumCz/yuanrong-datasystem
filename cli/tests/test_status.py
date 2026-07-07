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
"""Unit tests for the dscli status command parser."""

import importlib.util
import os
import sys
import types
import unittest

_CLI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATUS_PATH = os.path.join(_CLI_DIR, "status.py")


def _load_status_module():
    """Load cli/status.py in isolation.

    status.py imports ``yr.datasystem.cli.command`` and
    ``yr.datasystem.cli.common.util`` at module load time. Those packages only
    exist in a built/installed tree, so we stub them to exercise the pure
    ``parse_worker_lines`` helper directly from the source checkout.
    """
    for name in ["yr", "yr.datasystem", "yr.datasystem.cli",
                 "yr.datasystem.cli.common"]:
        sys.modules.setdefault(name, types.ModuleType(name))

    sys.modules["yr.datasystem.cli.common.util"] = types.ModuleType(
        "yr.datasystem.cli.common.util")

    command_mod = types.ModuleType("yr.datasystem.cli.command")

    class BaseCommand:
        SUCCESS = 0
        FAILURE = 1
        name = ""
        description = ""

        def __init__(self):
            pass

    command_mod.BaseCommand = BaseCommand
    sys.modules["yr.datasystem.cli.command"] = command_mod

    spec = importlib.util.spec_from_file_location(
        "ds_status_under_test", _STATUS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


status = _load_status_module()


class TestParseWorkerLines(unittest.TestCase):
    """Tests for status.parse_worker_lines (pgrep -fa output parser)."""

    def test_empty_output_returns_empty_list(self):
        self.assertEqual(status.parse_worker_lines(""), [])
        self.assertEqual(status.parse_worker_lines("   \n  \n"), [])

    def test_single_worker(self):
        output = "12345 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31501 --etcd_address=127.0.0.1:2379"
        self.assertEqual(
            status.parse_worker_lines(output),
            [(12345, "127.0.0.1:31501")],
        )

    def test_multiple_workers(self):
        output = (
            "12345 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31501\n"
            "12346 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31502 --log_dir=/var/log\n"
        )
        self.assertEqual(
            status.parse_worker_lines(output),
            [(12345, "127.0.0.1:31501"), (12346, "127.0.0.1:31502")],
        )

    def test_worker_wrapped_by_numactl(self):
        output = ("777 numactl --cpunodebind=0 /opt/ds/datasystem_worker "
                  "--worker_address=10.0.0.5:9000")
        self.assertEqual(
            status.parse_worker_lines(output),
            [(777, "10.0.0.5:9000")],
        )

    def test_skips_dscli_process(self):
        # A concurrent `dscli stop --worker_address=...` also matches the pgrep
        # pattern but must not be reported as a running worker.
        output = (
            "12345 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31501\n"
            "20001 python3 /usr/local/bin/dscli stop --worker_address=127.0.0.1:31501\n"
        )
        self.assertEqual(
            status.parse_worker_lines(output),
            [(12345, "127.0.0.1:31501")],
        )

    def test_ipv6_address(self):
        output = "42 /opt/ds/datasystem_worker --worker_address=[::1]:31501"
        self.assertEqual(
            status.parse_worker_lines(output),
            [(42, "[::1]:31501")],
        )

    def test_skips_malformed_lines(self):
        output = (
            "not_a_pid /opt/ds/datasystem_worker --worker_address=127.0.0.1:31501\n"
            "88888 /opt/ds/datasystem_worker --other_flag=1\n"
            "\n"
        )
        self.assertEqual(status.parse_worker_lines(output), [])


if __name__ == "__main__":
    unittest.main()

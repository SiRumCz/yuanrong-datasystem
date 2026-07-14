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
"""Unit tests for the dscli top command parser."""

import importlib.util
import os
import sys
import types
import unittest

_CLI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOP_PATH = os.path.join(_CLI_DIR, "top.py")


def _load_top_module():
    """Load cli/top.py in isolation by stubbing the package deps it imports."""
    for name in ["yr", "yr.datasystem", "yr.datasystem.cli",
                 "yr.datasystem.cli.common"]:
        sys.modules.setdefault(name, types.ModuleType(name))

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
        "ds_top_under_test", _TOP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


top = _load_top_module()


class TestParseWorkerLines(unittest.TestCase):
    """Tests for top.parse_worker_lines."""

    def test_single_worker(self):
        output = "12345 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31501"
        self.assertEqual(
            top.parse_worker_lines(output),
            [(12345, "127.0.0.1:31501")],
        )

    def test_multiple_workers(self):
        output = (
            "12345 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31501\n"
            "12346 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31502\n"
        )
        self.assertEqual(
            top.parse_worker_lines(output),
            [(12345, "127.0.0.1:31501"), (12346, "127.0.0.1:31502")],
        )


if __name__ == "__main__":
    unittest.main()

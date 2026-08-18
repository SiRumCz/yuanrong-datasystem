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
"""Unit tests for the dscli validate_config subcommand."""

import argparse
import copy
import importlib.util
import json
import logging
import os
import sys
import tempfile
import types
import unittest

_CLI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VALIDATE_PATH = os.path.join(_CLI_DIR, "validate_config.py")
_COMMAND_PATH = os.path.join(_CLI_DIR, "command.py")


def _load_validate_module():
    """Load cli/validate_config.py in isolation.

    validate_config.py imports ``yr.datasystem.cli.command`` and
    ``yr.datasystem.cli.common.util`` at module load time. Those packages only
    exist in a built/installed tree, so we stub them so the pure
    ``validate_cluster_config`` helper *and* ``Command.run`` can both be
    exercised directly from the source checkout.
    """
    for name in ["yr", "yr.datasystem", "yr.datasystem.cli",
                 "yr.datasystem.cli.common"]:
        sys.modules.setdefault(name, types.ModuleType(name))

    util_mod = types.ModuleType("yr.datasystem.cli.common.util")
    util_mod.valid_safe_path = lambda path: path  # identity for the tests
    sys.modules["yr.datasystem.cli.common.util"] = util_mod

    command_mod = types.ModuleType("yr.datasystem.cli.command")

    class BaseCommand:
        SUCCESS = 0
        FAILURE = 1
        name = ""
        description = ""
        logger = logging.getLogger("dscli-validate-config-test")

        def __init__(self):
            pass

    command_mod.BaseCommand = BaseCommand
    sys.modules["yr.datasystem.cli.command"] = command_mod

    spec = importlib.util.spec_from_file_location(
        "ds_validate_config_under_test", _VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_config = _load_validate_module()


# A structurally valid cluster config, matching the shape that
# `generate_config` writes (cli/deploy/conf/cluster_config.json).
_VALID_CONFIG = {
    "ssh_auth": {
        "ssh_private_key": "~/.ssh/id_rsa",
        "ssh_user_name": "root",
    },
    "worker_config_path": "./worker_config.json",
    "worker_nodes": ["127.0.0.1"],
    "worker_port": 31501,
    "metastore_head_node": "",
}


def _config_without(key):
    cfg = copy.deepcopy(_VALID_CONFIG)
    cfg.pop(key)
    return cfg


class TestValidateClusterConfig(unittest.TestCase):
    """Tests for the pure validate_config.validate_cluster_config helper."""

    def test_valid_config_returns_no_problems(self):
        self.assertEqual(validate_config.validate_cluster_config(_VALID_CONFIG), [])

    def test_non_dict_config(self):
        self.assertEqual(
            validate_config.validate_cluster_config(["not", "a", "dict"]),
            ["cluster config must be a JSON object"],
        )

    def test_missing_worker_nodes(self):
        self.assertIn(
            "worker_nodes is required",
            validate_config.validate_cluster_config(_config_without("worker_nodes")),
        )

    def test_empty_worker_nodes(self):
        cfg = copy.deepcopy(_VALID_CONFIG)
        cfg["worker_nodes"] = []
        self.assertIn(
            "worker_nodes must be a non-empty list",
            validate_config.validate_cluster_config(cfg),
        )

    def test_worker_nodes_with_blank_host(self):
        cfg = copy.deepcopy(_VALID_CONFIG)
        cfg["worker_nodes"] = ["127.0.0.1", "  "]
        self.assertIn(
            "worker_nodes must contain only non-empty host strings",
            validate_config.validate_cluster_config(cfg),
        )

    def test_worker_nodes_not_a_list(self):
        cfg = copy.deepcopy(_VALID_CONFIG)
        cfg["worker_nodes"] = "127.0.0.1"  # a bare string, not a list
        self.assertIn(
            "worker_nodes must be a non-empty list",
            validate_config.validate_cluster_config(cfg),
        )

    def test_missing_worker_port(self):
        self.assertIn(
            "worker_port is required",
            validate_config.validate_cluster_config(_config_without("worker_port")),
        )

    def test_worker_port_out_of_range(self):
        cfg = copy.deepcopy(_VALID_CONFIG)
        cfg["worker_port"] = 70000
        self.assertIn(
            "worker_port must be between 1 and 65535",
            validate_config.validate_cluster_config(cfg),
        )

    def test_worker_port_not_integer(self):
        cfg = copy.deepcopy(_VALID_CONFIG)
        cfg["worker_port"] = "31501"
        self.assertIn(
            "worker_port must be an integer",
            validate_config.validate_cluster_config(cfg),
        )

    def test_boolean_worker_port_rejected(self):
        # bool is a subclass of int; True must not be accepted as a port.
        cfg = copy.deepcopy(_VALID_CONFIG)
        cfg["worker_port"] = True
        self.assertIn(
            "worker_port must be an integer",
            validate_config.validate_cluster_config(cfg),
        )

    def test_missing_worker_config_path(self):
        self.assertIn(
            "worker_config_path must be a non-empty string",
            validate_config.validate_cluster_config(_config_without("worker_config_path")),
        )

    def test_missing_ssh_user_name(self):
        cfg = copy.deepcopy(_VALID_CONFIG)
        del cfg["ssh_auth"]["ssh_user_name"]
        self.assertIn(
            "ssh_auth.ssh_user_name must be a non-empty string",
            validate_config.validate_cluster_config(cfg),
        )

    def test_missing_ssh_private_key(self):
        cfg = copy.deepcopy(_VALID_CONFIG)
        del cfg["ssh_auth"]["ssh_private_key"]
        self.assertIn(
            "ssh_auth.ssh_private_key must be a non-empty string",
            validate_config.validate_cluster_config(cfg),
        )

    def test_blank_ssh_private_key(self):
        cfg = copy.deepcopy(_VALID_CONFIG)
        cfg["ssh_auth"]["ssh_private_key"] = "   "
        self.assertIn(
            "ssh_auth.ssh_private_key must be a non-empty string",
            validate_config.validate_cluster_config(cfg),
        )

    def test_non_dict_ssh_auth(self):
        cfg = copy.deepcopy(_VALID_CONFIG)
        cfg["ssh_auth"] = "root@host"
        self.assertIn(
            "ssh_auth must be an object with SSH credentials",
            validate_config.validate_cluster_config(cfg),
        )

    def test_multiple_problems_accumulate(self):
        cfg = {"worker_nodes": [], "worker_port": 0}
        problems = validate_config.validate_cluster_config(cfg)
        self.assertIn("worker_nodes must be a non-empty list", problems)
        self.assertIn("worker_port must be between 1 and 65535", problems)
        self.assertIn("worker_config_path must be a non-empty string", problems)
        self.assertIn("ssh_auth must be an object with SSH credentials", problems)
        self.assertEqual(len(problems), 4)


class TestValidateConfigCommandRun(unittest.TestCase):
    """Tests for validate_config.Command.run (file load + return codes)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.command = validate_config.Command()

    def tearDown(self):
        for name in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def _run(self, path):
        return self.command.run(argparse.Namespace(config=path))

    def test_run_valid_config_returns_success(self):
        path = self._write("valid.json", json.dumps(_VALID_CONFIG))
        self.assertEqual(self._run(path), validate_config.Command.SUCCESS)

    def test_run_invalid_config_returns_failure(self):
        cfg = {"worker_nodes": [], "worker_port": 70000}
        path = self._write("invalid.json", json.dumps(cfg))
        self.assertEqual(self._run(path), validate_config.Command.FAILURE)

    def test_run_missing_file_returns_failure(self):
        path = os.path.join(self.tmpdir, "does_not_exist.json")
        self.assertEqual(self._run(path), validate_config.Command.FAILURE)

    def test_run_unreadable_path_returns_failure(self):
        # A path that is a directory (not a file) trips the OSError fallback.
        self.assertEqual(self._run(self.tmpdir), validate_config.Command.FAILURE)

    def test_run_malformed_json_returns_failure(self):
        path = self._write("bad.json", "{not valid json")
        self.assertEqual(self._run(path), validate_config.Command.FAILURE)


class TestValidateConfigArguments(unittest.TestCase):
    """Tests for validate_config.Command.add_arguments (flag wiring + default)."""

    @staticmethod
    def _parse(argv):
        parser = argparse.ArgumentParser()
        validate_config.Command.add_arguments(parser)
        return parser.parse_args(argv)

    def test_config_defaults_to_cwd_cluster_config(self):
        args = self._parse([])
        self.assertEqual(
            args.config, os.path.join(os.getcwd(), "cluster_config.json"))

    def test_config_short_flag(self):
        args = self._parse(["-c", "/tmp/custom.json"])
        self.assertEqual(args.config, "/tmp/custom.json")

    def test_config_long_flag(self):
        args = self._parse(["--config", "/tmp/other.json"])
        self.assertEqual(args.config, "/tmp/other.json")


class TestValidateConfigRegistration(unittest.TestCase):
    """The subcommand must be wired into the CLI entry point."""

    def test_command_registered_in_modules(self):
        with open(_COMMAND_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"validate_config"', source)


if __name__ == "__main__":
    unittest.main()

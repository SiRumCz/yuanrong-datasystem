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
"""Unit tests for the dscli metastore_ha subcommand."""

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
_METASTORE_PATH = os.path.join(_CLI_DIR, "metastore_ha.py")
_COMMAND_PATH = os.path.join(_CLI_DIR, "command.py")


def _load_metastore_module():
    """Load cli/metastore_ha.py in isolation.

    metastore_ha.py imports ``yr.datasystem.cli.command`` and
    ``yr.datasystem.cli.common.util`` at module load time. Those packages only
    exist in a built/installed tree, so we stub them so the pure helpers *and*
    ``Command.run`` can be exercised directly from the source checkout.
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
        logger = logging.getLogger("dscli-metastore-ha-test")

        def __init__(self):
            pass

    command_mod.BaseCommand = BaseCommand
    sys.modules["yr.datasystem.cli.command"] = command_mod

    spec = importlib.util.spec_from_file_location(
        "ds_metastore_ha_under_test", _METASTORE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metastore_ha = _load_metastore_module()


# A structurally valid cluster config with several worker nodes, so Metastore
# head election has something to choose from.
_VALID_CONFIG = {
    "ssh_auth": {
        "ssh_private_key": "~/.ssh/id_rsa",
        "ssh_user_name": "root",
    },
    "worker_config_path": "./worker_config.json",
    "worker_nodes": ["127.0.0.1", "127.0.0.2", "127.0.0.3"],
    "worker_port": 31501,
    "metastore_head_node": "127.0.0.1",
}


class TestSelectMetastoreHeads(unittest.TestCase):
    """Tests for the pure metastore_ha.select_metastore_heads helper."""

    def test_selects_first_n_nodes(self):
        heads = metastore_ha.select_metastore_heads(["a", "b", "c", "d"], 3)
        self.assertEqual(heads, ["a", "b", "c"])

    def test_replicas_capped_at_node_count(self):
        heads = metastore_ha.select_metastore_heads(["a", "b"], 10)
        self.assertEqual(heads, ["a", "b"])

    def test_replicas_floored_at_minimum(self):
        heads = metastore_ha.select_metastore_heads(["a", "b", "c"], 1)
        self.assertEqual(heads, ["a", "b"])

    def test_empty_worker_nodes(self):
        self.assertEqual(metastore_ha.select_metastore_heads([], 3), [])


class TestBuildHaMetastoreConfig(unittest.TestCase):
    """Tests for metastore_ha.build_ha_metastore_config."""

    def test_replaces_single_head_with_head_list(self):
        ha = metastore_ha.build_ha_metastore_config(_VALID_CONFIG, 2)
        self.assertEqual(ha["metastore_head_nodes"], ["127.0.0.1", "127.0.0.2"])
        self.assertNotIn("metastore_head_node", ha)

    def test_does_not_mutate_source_config(self):
        source = copy.deepcopy(_VALID_CONFIG)
        metastore_ha.build_ha_metastore_config(source, 2)
        self.assertEqual(source, _VALID_CONFIG)

    def test_preserves_other_fields(self):
        ha = metastore_ha.build_ha_metastore_config(_VALID_CONFIG, 2)
        self.assertEqual(ha["worker_port"], 31501)
        self.assertEqual(ha["worker_config_path"], "./worker_config.json")


class TestMetastoreHaCommandRun(unittest.TestCase):
    """Tests for metastore_ha.Command.run (file load, transform, write, codes)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.command = metastore_ha.Command()
        self._orig_valid_safe_path = metastore_ha.util.valid_safe_path

    def tearDown(self):
        metastore_ha.util.valid_safe_path = self._orig_valid_safe_path
        for name in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def _run(self, config_path, output_name="cluster_config_ha.json", replicas=3):
        output_path = os.path.join(self.tmpdir, output_name)
        return self.command.run(argparse.Namespace(
            config=config_path, replicas=replicas, output=output_path))

    def test_run_valid_config_returns_success_and_writes_output(self):
        path = self._write("cluster_config.json", json.dumps(_VALID_CONFIG))
        self.assertEqual(self._run(path), metastore_ha.Command.SUCCESS)
        with open(os.path.join(self.tmpdir, "cluster_config_ha.json")) as handle:
            written = json.load(handle)
        self.assertEqual(written["metastore_head_nodes"], ["127.0.0.1", "127.0.0.2", "127.0.0.3"])
        self.assertNotIn("metastore_head_node", written)

    def test_run_missing_file_returns_failure(self):
        path = os.path.join(self.tmpdir, "does_not_exist.json")
        self.assertEqual(self._run(path), metastore_ha.Command.FAILURE)

    def test_run_malformed_json_returns_failure(self):
        path = self._write("bad.json", "{not valid json")
        self.assertEqual(self._run(path), metastore_ha.Command.FAILURE)

    def test_run_non_object_config_returns_failure(self):
        path = self._write("list.json", json.dumps(["not", "an", "object"]))
        self.assertEqual(self._run(path), metastore_ha.Command.FAILURE)

    def test_run_unreadable_config_path_returns_failure(self):
        # A directory (not a file) trips the OSError read arm.
        self.assertEqual(self._run(self.tmpdir), metastore_ha.Command.FAILURE)

    def test_run_rejected_unsafe_path_returns_failure(self):
        # When util.valid_safe_path rejects the path, the ValueError arm fires.
        def _reject(_path):
            raise ValueError("unsafe path")
        metastore_ha.util.valid_safe_path = _reject
        path = self._write("cluster_config.json", json.dumps(_VALID_CONFIG))
        self.assertEqual(self._run(path), metastore_ha.Command.FAILURE)

    def test_run_unwritable_output_returns_failure(self):
        # Output path is a directory → open(..., "w") raises the OSError write arm.
        path = self._write("cluster_config.json", json.dumps(_VALID_CONFIG))
        rc = self.command.run(argparse.Namespace(
            config=path, replicas=3, output=self.tmpdir))
        self.assertEqual(rc, metastore_ha.Command.FAILURE)


class TestMetastoreHaRegistration(unittest.TestCase):
    """The subcommand must be wired into the CLI entry point."""

    def test_command_registered_in_modules(self):
        with open(_COMMAND_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"metastore_ha"', source)


if __name__ == "__main__":
    unittest.main()

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
"""Pytest configuration for CLI unit tests.

Sets up the yr.datasystem.cli namespace from the repo-root cli/ directory so
tests can run without a full package install.
"""

import importlib.metadata
import importlib.resources
import os
import sys
import types
from pathlib import Path

_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_cli_dir = os.path.join(_repo_root, "cli")

if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Patch importlib.metadata.version before yr.datasystem.cli.__init__ is imported
_orig_meta_version = importlib.metadata.version


def _patched_version(name):
    if name == "openyuanrong-datasystem":
        return "0.0.0-test"
    return _orig_meta_version(name)


importlib.metadata.version = _patched_version

# Patch importlib.resources.files so BaseCommand.__init__ can resolve yr.datasystem
_orig_resources_files = importlib.resources.files


def _patched_files(package):
    if package == "yr.datasystem":
        return Path(_repo_root)
    return _orig_resources_files(package)


importlib.resources.files = _patched_files

# Wire yr.datasystem.cli → cli/ so all sub-imports resolve correctly
if "yr" not in sys.modules:
    _yr = types.ModuleType("yr")
    _yr.__path__ = []
    _yr.__package__ = "yr"
    sys.modules["yr"] = _yr

if "yr.datasystem" not in sys.modules:
    _yr_ds = types.ModuleType("yr.datasystem")
    _yr_ds.__path__ = []
    _yr_ds.__package__ = "yr.datasystem"
    sys.modules["yr.datasystem"] = _yr_ds

if "yr.datasystem.cli" not in sys.modules:
    _yr_ds_cli = types.ModuleType("yr.datasystem.cli")
    _yr_ds_cli.__path__ = [_cli_dir]
    _yr_ds_cli.__package__ = "yr.datasystem.cli"
    _yr_ds_cli.__version__ = "0.0.0-test"
    sys.modules["yr.datasystem.cli"] = _yr_ds_cli

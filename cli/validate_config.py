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
"""YuanRong datasystem CLI validate_config command."""

import json
import os

from yr.datasystem.cli.command import BaseCommand
import yr.datasystem.cli.common.util as util

# A cluster config (the cluster_config.json that `generate_config` writes and the
# operator then hand-edits) must name at least one worker node, a valid worker
# port, the path to the per-worker config, and an SSH identity for multi-node
# fan-out. These are exactly the fields whose absence or malformed values
# otherwise surface late, mid-deploy, instead of up front.
_MIN_PORT = 1
_MAX_PORT = 65535


def validate_cluster_config(config):
    """Validate a parsed cluster config, returning a list of problems.

    Args:
        config (dict): The parsed contents of a ``cluster_config.json`` file.

    Returns:
        list[str]: One human-readable message per problem found, in a stable
            order. An empty list means the config is structurally valid.
    """
    if not isinstance(config, dict):
        return ["cluster config must be a JSON object"]

    problems = []

    worker_nodes = config.get("worker_nodes")
    if worker_nodes is None:
        problems.append("worker_nodes is required")
    elif not isinstance(worker_nodes, list) or not worker_nodes:
        problems.append("worker_nodes must be a non-empty list")
    elif any(not isinstance(node, str) or not node.strip() for node in worker_nodes):
        problems.append("worker_nodes must contain only non-empty host strings")

    worker_port = config.get("worker_port")
    if worker_port is None:
        problems.append("worker_port is required")
    elif isinstance(worker_port, bool) or not isinstance(worker_port, int):
        problems.append("worker_port must be an integer")
    elif not _MIN_PORT <= worker_port <= _MAX_PORT:
        problems.append(f"worker_port must be between {_MIN_PORT} and {_MAX_PORT}")

    worker_config_path = config.get("worker_config_path")
    if not isinstance(worker_config_path, str) or not worker_config_path.strip():
        problems.append("worker_config_path must be a non-empty string")

    ssh_auth = config.get("ssh_auth")
    if not isinstance(ssh_auth, dict):
        problems.append("ssh_auth must be an object with SSH credentials")
    else:
        for key in ("ssh_user_name", "ssh_private_key"):
            value = ssh_auth.get(key)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"ssh_auth.{key} must be a non-empty string")

    return problems


class Command(BaseCommand):
    """
    Validate a yuanrong datasystem cluster configuration file.
    """

    name = "validate_config"
    description = "validate a yuanrong datasystem cluster configuration file"

    @staticmethod
    def add_arguments(parser):
        """
        Add arguments to parser.

        Args:
            parser (ArgumentParser): Specify parser to which arguments are added.
        """
        parser.add_argument(
            "-c", "--config",
            default=os.path.join(os.getcwd(), "cluster_config.json"),
            help="path to the cluster configuration file to validate, "
                 "default is ./cluster_config.json"
        )

    def run(self, args):
        """
        Execute the validate_config command.

        Args:
            args (Namespace): Parsed arguments containing the config path.

        Returns:
            int: BaseCommand.SUCCESS if the config is valid; BaseCommand.FAILURE
                if it is missing, unreadable, or invalid.
        """
        config_path = os.path.normpath(os.path.realpath(args.config))
        try:
            config_path = util.valid_safe_path(config_path)
            with open(config_path, "r", encoding="utf-8") as config_file:
                config = json.load(config_file)
        except FileNotFoundError:
            self.logger.error(f"Configuration file not found: {config_path}")
            return self.FAILURE
        except json.JSONDecodeError as e:
            self.logger.error(f"Configuration file is not valid JSON: {e}")
            return self.FAILURE
        except (OSError, ValueError) as e:
            self.logger.error(f"Failed to read configuration file: {e}")
            return self.FAILURE

        problems = validate_cluster_config(config)
        if problems:
            self.logger.error(
                f"Configuration is invalid: {len(problems)} problem(s) found in {config_path}"
            )
            for problem in problems:
                self.logger.error(f"  - {problem}")
            return self.FAILURE

        self.logger.info(f"Configuration is valid: {config_path}")
        return self.SUCCESS

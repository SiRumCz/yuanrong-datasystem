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


class Command(BaseCommand):
    """
    List running yuanrong datasystem worker services on the local host.
    """

    name = "status"
    description = "list running yuanrong datasystem worker services on this host"

    _worker_address_flag = "worker_address"
    _pgrep_timeout = 5

    def run(self, args):
        """
        Execute for status command.

        Args:
            args (Namespace): Parsed arguments to hold customized parameters.

        Returns:
            int: Exit code, 0 for success, 1 for failure.
        """
        try:
            workers = self.list_workers()
        except Exception as e:
            self.logger.error(f"Status failed: {e}")
            return self.FAILURE

        if not workers:
            self.logger.info("No running datasystem worker services found.")
            return self.SUCCESS

        self.report_workers(workers)
        return self.SUCCESS

    def list_workers(self):
        """
        Discover running datasystem worker processes on the local host.

        Reuses the same discovery signal as the stop command: worker processes
        are identified by the ``-worker_address=`` argument in their command
        line. Unlike stop, every match is returned instead of requiring a
        single unique process.

        Returns:
            list[tuple[str, int]]: (worker_address, pid) pairs sorted by
            address then pid.
        """
        workers = []
        for pid in self.find_candidate_pids():
            address = self.get_value_from_process_cmdline(pid, self._worker_address_flag)
            if not address:
                continue
            workers.append((address, pid))
        return sorted(workers)

    def find_candidate_pids(self):
        """
        Find candidate worker PIDs by scanning command lines for the
        ``-worker_address=`` marker.

        Returns:
            list[int]: Candidate process IDs (may include false positives that
            only mention the marker; callers must confirm via cmdline parsing).
        """
        target_arg = re.escape(f"-{self._worker_address_flag}=")
        cmd = ["pgrep", "-fl", "--", target_arg]
        try:
            output = subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT,
                timeout=self._pgrep_timeout,
                text=True,
            )
        except subprocess.CalledProcessError:
            # pgrep exits non-zero when nothing matches; that is not an error.
            return []

        pids = []
        for line in output.strip().splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            current_pid, pid_name = parts
            if pid_name == "dscli":
                continue
            try:
                pids.append(int(current_pid))
            except ValueError:
                continue
        return pids

    def get_value_from_process_cmdline(self, pid, key):
        """
        Read the value of a flag from a process command line.

        Args:
            pid (int): Process ID.
            key (str): Flag key (without leading dashes).

        Returns:
            Optional[str]: Flag value if present, otherwise None.
        """
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw_cmdline = f.read()
        except OSError:
            return None

        if not raw_cmdline:
            return None

        args = [arg.decode("utf-8", errors="ignore") for arg in raw_cmdline.split(b"\x00") if arg]

        prefixes = (f"--{key}=", f"-{key}=")
        for arg in args:
            for prefix in prefixes:
                if arg.startswith(prefix):
                    return arg[len(prefix):]

        options = (f"--{key}", f"-{key}")
        for i, arg in enumerate(args[:-1]):
            if arg in options:
                return args[i + 1]
        return None

    def report_workers(self, workers):
        """
        Print a human-readable table of running worker services.

        Args:
            workers (list[tuple[str, int]]): (worker_address, pid) pairs.
        """
        addr_header = "WORKER_ADDRESS"
        addr_width = max(len(addr_header), *(len(address) for address, _ in workers))
        lines = [
            f"Found {len(workers)} running datasystem worker service(s):",
            f"  {addr_header.ljust(addr_width)}  PID",
        ]
        for address, pid in workers:
            lines.append(f"  {address.ljust(addr_width)}  {pid}")
        self.logger.info("\n".join(lines))

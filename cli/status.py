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
import subprocess

from yr.datasystem.cli.command import BaseCommand


class Command(BaseCommand):
    """
    List running yuanrong datasystem worker services on the local host.
    """

    name = "status"
    description = "list running yuanrong datasystem worker services on the local host"

    _WORKER_BINARY = "datasystem_worker"
    _WORKER_ADDRESS_KEY = "worker_address"

    def add_arguments(self, parser):
        """
        Add arguments to parser.

        Args:
            parser (ArgumentParser): Specify parser to which arguments are added.
        """
        parser.add_argument(
            "-j", "--json", action="store_true", default=False,
            help="output the worker list as JSON instead of a table",
        )

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

        if args.json:
            print(json.dumps(workers))
            return self.SUCCESS

        self.print_table(workers)
        return self.SUCCESS

    def list_workers(self):
        """
        Discover running datasystem worker services on the local host.

        Returns:
            list[dict]: One entry per worker with 'address', 'pid', and
                'uptime_seconds' keys, sorted by address then pid.
        """
        workers = []
        for pid in self.find_worker_pids():
            cmdline = self.read_cmdline(pid)
            if not cmdline:
                continue
            # Only report the worker binary itself, so a concurrent
            # "dscli stop -w <addr>" invocation is never mistaken for a worker.
            if os.path.basename(cmdline[0]) != self._WORKER_BINARY:
                continue
            address = self.extract_flag(cmdline, self._WORKER_ADDRESS_KEY)
            if address is None:
                continue
            workers.append({
                "address": address,
                "pid": pid,
                "uptime_seconds": self.get_uptime_seconds(pid),
            })
        workers.sort(key=lambda w: (w["address"], w["pid"]))
        return workers

    def find_worker_pids(self):
        """
        Find candidate PIDs whose command line carries a worker_address flag.

        Returns:
            list[int]: Candidate process IDs (may include non-worker processes
                that are filtered out later by binary name).
        """
        cmd = ["pgrep", "-f", "--", f"-{self._WORKER_ADDRESS_KEY}="]
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5, text=True)
        except subprocess.CalledProcessError:
            # pgrep exits with 1 when there is no match; that is not an error here.
            return []

        pids = []
        for line in output.split():
            try:
                pid = int(line)
            except ValueError:
                continue
            if pid != os.getpid():
                pids.append(pid)
        return pids

    @staticmethod
    def read_cmdline(pid):
        """
        Read the argument vector of a process from /proc.

        Args:
            pid (int): Process ID.

        Returns:
            list[str]: Decoded command-line arguments, or an empty list if the
                process cmdline cannot be read (e.g. the process has exited).
        """
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw_cmdline = f.read()
        except OSError:
            return []
        return [arg.decode("utf-8", errors="ignore") for arg in raw_cmdline.split(b"\x00") if arg]

    @staticmethod
    def extract_flag(cmdline, key):
        """
        Extract a flag value from an argument vector.

        Supports both "--key=value"/"-key=value" and "--key value"/"-key value".

        Args:
            cmdline (list[str]): Decoded command-line arguments.
            key (str): Flag key.

        Returns:
            Optional[str]: Flag value if present.
        """
        prefixes = (f"--{key}=", f"-{key}=")
        for arg in cmdline:
            for prefix in prefixes:
                if arg.startswith(prefix):
                    return arg[len(prefix):]

        options = (f"--{key}", f"-{key}")
        for i, arg in enumerate(cmdline[:-1]):
            if arg in options:
                return cmdline[i + 1]
        return None

    @staticmethod
    def get_uptime_seconds(pid):
        """
        Compute how long a process has been running, in seconds.

        Args:
            pid (int): Process ID.

        Returns:
            Optional[int]: Elapsed seconds since the process started, or None
                if it cannot be determined.
        """
        try:
            with open(f"/proc/{pid}/stat", "rb") as f:
                stat_data = f.read()
            with open("/proc/uptime", "r") as f:
                system_uptime = float(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            return None

        # The comm field (index 2) is wrapped in parentheses and may contain
        # spaces, so parse the fields that follow the final ')'.
        rparen = stat_data.rfind(b")")
        if rparen == -1:
            return None
        try:
            fields = stat_data[rparen + 1:].split()
            # starttime is field 22; after the ')' the split starts at field 3.
            starttime_ticks = int(fields[19])
        except (IndexError, ValueError):
            return None

        hz = os.sysconf("SC_CLK_TCK")
        if hz <= 0:
            return None
        uptime = system_uptime - starttime_ticks / hz
        return int(uptime) if uptime >= 0 else None

    @staticmethod
    def format_uptime(seconds):
        """
        Render an uptime in seconds as [D-]H:MM:SS, matching ps(1) etime style.

        Args:
            seconds (Optional[int]): Elapsed seconds, or None if unknown.

        Returns:
            str: Human-readable uptime, or '-' when unknown.
        """
        if seconds is None:
            return "-"
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        if days:
            return f"{days}-{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{hours}:{minutes:02d}:{secs:02d}"

    def print_table(self, workers):
        """
        Print the worker list as an aligned table.

        Args:
            workers (list[dict]): Worker entries from list_workers().
        """
        if not workers:
            self.logger.info("No running datasystem worker service found")
            return

        addr_width = max(len("ADDRESS"), max(len(w["address"]) for w in workers))
        pid_width = max(len("PID"), max(len(str(w["pid"])) for w in workers))
        print(f"{'ADDRESS':<{addr_width}}  {'PID':<{pid_width}}  UPTIME")
        for w in workers:
            print(
                f"{w['address']:<{addr_width}}  {w['pid']:<{pid_width}}  "
                f"{self.format_uptime(w['uptime_seconds'])}"
            )

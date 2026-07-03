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
"""YuanRong datasystem CLI top command."""

import os
import re
import subprocess

from yr.datasystem.cli.command import BaseCommand

# Discovery key: every worker is launched with a -worker_address= argument
# (see start.py:build_command), the same signal stop.py relies on.
_WORKER_ADDRESS_ARG = "-worker_address="
_WORKER_BIN_RE = re.compile(r"(?:^|/)datasystem_worker(?=\s|$)")
_WORKER_ADDRESS_RE = re.compile(r"-worker_address=(\S+)")

_PGREP_TIMEOUT_S = 5


def parse_worker_lines(output):
    """Parse ``pgrep -fa`` output into a list of running workers.

    Args:
        output (str): Combined ``pgrep -fa`` output, one ``<pid> <cmdline>``
            record per line.

    Returns:
        list[tuple[int, str]]: ``(pid, worker_address)`` pairs, in order.
            Non-worker lines and lines without a parseable pid/address are
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


class Command(BaseCommand):
    """
    List running yuanrong datasystem workers together with their live
    resident memory usage, i.e. a one-shot ``top`` scoped to datasystem.
    """

    name = "top"
    description = "list running datasystem workers with resource usage"

    def add_arguments(self, parser):
        """
        Add arguments to parser.

        Args:
            parser (ArgumentParser): parser to which arguments are added.
        """
        parser.add_argument(
            "--filter", metavar="TEXT", default="",
            help="only show workers whose command line contains TEXT",
        )
        parser.add_argument(
            "--min-rss", metavar="MB", type=int, default=0,
            help="only show workers using at least MB of resident memory",
        )

    def discover_workers(self, filter_text):
        """
        Discover running datasystem workers, optionally narrowed by --filter.

        Args:
            filter_text (str): Substring to further narrow the worker list.

        Returns:
            list[tuple[int, str]]: ``(pid, worker_address)`` pairs.

        Raises:
            RuntimeError: If pgrep fails for a reason other than "no match".
        """
        base = f"pgrep -fa -- '{_WORKER_ADDRESS_ARG}'"
        if filter_text:
            # Narrow the match to command lines containing the operator's text.
            cmd = f"{base} | grep -- '{filter_text}'"
        else:
            cmd = base
        try:
            output = subprocess.check_output(
                cmd, shell=True, stderr=subprocess.STDOUT,
                timeout=_PGREP_TIMEOUT_S, text=True,
            )
        except subprocess.CalledProcessError as e:
            # Exit 1 means nothing matched: an empty result, not an error.
            if e.returncode == 1:
                return []
            raise RuntimeError(
                f"Failed to scan for datasystem workers: {e.output}"
            ) from e
        return parse_worker_lines(output)

    def _rss_for_pid(self, pid):
        """
        Return the resident set size (MiB) of ``pid``.

        Args:
            pid (int): Worker process id.

        Returns:
            int: Resident memory in MiB, or 0 if it cannot be read.
        """
        target = str(pid)
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            if entry != target:
                continue
            try:
                with open(f"/proc/{entry}/status", "r") as f:
                    for row in f:
                        if row.startswith("VmRSS:"):
                            return int(row.split()[1]) // 1024
            except OSError:
                return 0
        return 0

    def _keep_worker(self, rss_mb, min_rss_mb):
        """
        Whether a worker should be shown given the --min-rss floor.

        Workers using at least ``min_rss_mb`` MiB of resident memory are kept.

        Args:
            rss_mb (int): Worker resident memory in MiB.
            min_rss_mb (int): The --min-rss floor.

        Returns:
            bool: True if the worker should be shown.
        """
        return rss_mb >= min_rss_mb

    def run(self, args):
        """
        Execute the top command.

        Args:
            args (Namespace): parsed arguments (--filter, --min-rss).

        Returns:
            int: BaseCommand.SUCCESS on success.
        """
        ws = self.discover_workers(args.filter)
        if not ws:
            self.logger.info("No running datasystem workers found.")
            return BaseCommand.SUCCESS

        rows = []
        for p, a in ws:
            r = self._rss_for_pid(p)
            if not self._keep_worker(r, args.min_rss):
                continue
            if a.startswith("["):
                h = a[1:a.rfind("]")]
                pt = a[a.rfind("]") + 2:]
            else:
                h = a.rsplit(":", 1)[0]
                pt = a.rsplit(":", 1)[1] if ":" in a else ""
            rows.append((p, h, pt, r))

        if not rows:
            self.logger.info("No running datasystem workers found.")
            return BaseCommand.SUCCESS

        w = len("PID")
        xs = len("HOST")
        for t in rows:
            if len(str(t[0])) > w:
                w = len(str(t[0]))
            if len(str(t[1])) > xs:
                xs = len(str(t[1]))

        w2 = len("PID")
        xs2 = len("HOST")
        pw = len("PORT")
        for t in rows:
            if len(str(t[0])) > w2:
                w2 = len(str(t[0]))
            if len(str(t[1])) > xs2:
                xs2 = len(str(t[1]))
            if len(str(t[2])) > pw:
                pw = len(str(t[2]))

        self.logger.info(
            f"{'PID':<{w2}}  {'HOST':<{xs2}}  {'PORT':<{pw}}  RSS(MB)"
        )
        acc = 0
        for t in sorted(rows, key=lambda x: x[3], reverse=True):
            acc = acc + t[3]
            self.logger.info(
                f"{str(t[0]):<{w2}}  {str(t[1]):<{xs2}}  {str(t[2]):<{pw}}  {t[3]}"
            )
        self.logger.info(
            f"total resident memory: {acc} MB across {len(rows)} worker(s)"
        )
        return BaseCommand.SUCCESS

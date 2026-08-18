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
"""``dscli logs`` subcommand: print recent log lines for a worker service."""

import argparse
import json
import os
import re
import subprocess

import yr.datasystem.cli.common.util as util
from yr.datasystem.cli.command import BaseCommand

# Every worker is launched with a ``-worker_address=`` argument; reuse that
# signal (as ``stop`` does) to locate the running process.
_WORKER_ADDRESS_ARG = "-worker_address="
_PGREP_TIMEOUT_S = 5
_DEFAULT_LINES = 100
_DEFAULT_WORKER_PROCESS_NAME = "datasystem_worker"
_FALLBACK_LOG_DIR = "~/.datasystem/logs"
_INFO_LOG_SUFFIX = ".INFO.log"
_WORKER_ADDRESS_RE = re.compile(r"^[A-Za-z0-9.-]+:[0-9]+$")
_MIN_PORT = 1
_MAX_PORT = 65535


def _parse_positive_int(value):
    """Return ``value`` as a positive integer for argparse validation."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("lines must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("lines must be a positive integer")
    return parsed


def _validate_worker_address(address):
    """Validate and return a worker address in host:port form."""
    if not isinstance(address, str) or not _WORKER_ADDRESS_RE.fullmatch(address):
        raise ValueError("worker_address must be in host:port form")
    port = int(address.rsplit(":", 1)[1])
    if port < _MIN_PORT or port > _MAX_PORT:
        raise ValueError("worker_address port must be between 1 and 65535")
    return address


class Command(BaseCommand):
    """Print the last N lines of a worker's log file to stdout."""

    name = "logs"
    description = "print recent log lines for a worker service"

    def add_arguments(self, parser):
        """Add ``logs`` arguments to the parser."""
        parser.add_argument(
            "-w", "--worker_address", metavar="ADDR", required=True,
            help="address (host:port) of the worker whose log to print",
        )
        parser.add_argument(
            "-n", "--lines", metavar="N", default=_DEFAULT_LINES, type=_parse_positive_int,
            help="number of trailing log lines to print",
        )

    def run(self, args):
        """Print the tail of the worker's log to stdout."""
        try:
            address = _validate_worker_address(args.worker_address)
            lines = _parse_positive_int(args.lines)
        except (ValueError, argparse.ArgumentTypeError) as exc:
            self.logger.error(str(exc))
            return BaseCommand.FAILURE

        pid = self.find_worker_pid(address)
        if pid is None:
            self.logger.warning(f"No running worker @ {address}; showing last log")

        log_path = self.resolve_log_path(pid)
        result = subprocess.run(["tail", "-n", str(lines), log_path])
        if result.returncode != 0:
            self.logger.error(f"Failed to read log for {address}")
            return BaseCommand.FAILURE
        return BaseCommand.SUCCESS

    def resolve_log_path(self, pid):
        """Resolve the worker INFO log path from cmdline flags or config defaults."""
        args = self.get_process_args(pid) if pid is not None else []
        log_dir = self.get_flag_value(args, "log_dir")
        log_filename = self.get_flag_value(args, "log_filename")
        defaults = self.load_worker_config_defaults()
        if not log_dir:
            log_dir = defaults.get("log_dir", _FALLBACK_LOG_DIR)
        if not log_filename:
            log_filename = defaults.get("log_filename", "")
        log_dir = self.resolve_default_path(log_dir)
        if not log_filename:
            log_filename = self.get_process_name(args)
        return os.path.join(log_dir, f"{log_filename}{_INFO_LOG_SUFFIX}")

    def load_worker_config_defaults(self):
        """Load default worker config values used by ``dscli start``."""
        defaults = {}
        try:
            util.fill_worker_config_defaults(self._base_dir, defaults)
        except (OSError, json.JSONDecodeError):
            return {}
        return defaults

    def resolve_default_path(self, path):
        """Normalize default relative paths the same way ``dscli start`` does."""
        if path.startswith("./"):
            path = util.get_timestamped_path(path)
        return os.path.realpath(os.path.expanduser(path))

    def get_process_args(self, pid):
        """Read process argv from procfs, returning an empty list if it vanished."""
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw_cmdline = f.read()
        except OSError:
            return []
        return [arg.decode("utf-8", errors="ignore") for arg in raw_cmdline.split(b"\x00") if arg]

    def get_flag_value(self, args, key):
        """Return a gflag value from process argv, if present."""
        prefixes = (f"--{key}=", f"-{key}=")
        for arg in args:
            for prefix in prefixes:
                if arg.startswith(prefix):
                    return arg[len(prefix):]
        options = (f"--{key}", f"-{key}")
        for idx, arg in enumerate(args[:-1]):
            if arg in options:
                return args[idx + 1]
        return None

    def get_process_name(self, args):
        """Return argv[0]'s basename or the default worker process name."""
        if not args:
            return _DEFAULT_WORKER_PROCESS_NAME
        process_name = os.path.basename(args[0])
        return process_name or _DEFAULT_WORKER_PROCESS_NAME

    def find_worker_pid(self, address):
        """Return the PID of the worker at ``address``, or ``None`` if not found."""
        target = re.escape(f"{_WORKER_ADDRESS_ARG}{address}")
        cmd = ["pgrep", "-fa", "--", target]
        try:
            output = subprocess.check_output(
                cmd, stderr=subprocess.STDOUT, timeout=_PGREP_TIMEOUT_S, text=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        for line in output.strip().splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2 and "dscli" not in parts[1]:
                return int(parts[0])
        return None

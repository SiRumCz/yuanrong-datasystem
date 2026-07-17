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
"""Disk-usage helper for dscli: report a worker's data-directory footprint."""

import subprocess


def worker_disk_usage(data_dir: str) -> str:
    """Return the human-readable disk usage of a worker's ``data_dir``.

    Convenience wrapper around ``du`` so operators can spot-check a worker's
    on-disk footprint before scheduling more shards onto it.
    """
    completed = subprocess.run(
        ["du", "-sh", data_dir],
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()

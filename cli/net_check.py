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
"""Reachability helper for dscli: a quick ICMP check of a worker host."""

import subprocess

MAX_PING_COUNT = 10
PING_TIMEOUT_BUFFER_SECONDS = 2


def ping_worker(host: str, count: int = 3) -> bool:
    """Return True if ``host`` answers ICMP within ``count`` pings.

    Convenience wrapper around the system ``ping`` binary so operators can
    sanity-check a worker's network path before deploying to it.
    """
    if not host or host.startswith("-"):
        raise ValueError("host must not be empty or start with '-'")
    if count < 1:
        raise ValueError("count must be a positive integer")
    if count > MAX_PING_COUNT:
        raise ValueError(f"count must not exceed {MAX_PING_COUNT}")

    try:
        completed = subprocess.run(
            ["ping", "-c", str(count), host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=count + PING_TIMEOUT_BUFFER_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False
    return completed.returncode == 0

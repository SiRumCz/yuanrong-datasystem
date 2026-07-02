/**
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * Description: Pure helper to select stale cache entries by age. Selection only — never removes.
 */
#ifndef DATASYSTEM_COMMON_UTIL_STALE_SCAN_H
#define DATASYSTEM_COMMON_UTIL_STALE_SCAN_H

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace datasystem {
// One scanned entry: its path and last-modified time in epoch seconds.
using ScanEntry = std::pair<std::string, int64_t>;

/**
 * @brief Select the entries whose last-modified time is strictly older than a cutoff.
 *
 * Pure computation: given a snapshot of (path, mtime) entries, returns the paths considered stale
 * (mtime < cutoffEpochSec), preserving input order. The caller decides what to do with the result;
 * this helper performs no filesystem access and removes nothing.
 *
 * @param[in] entries        Snapshot of scanned entries (path + mtime in epoch seconds).
 * @param[in] cutoffEpochSec Age boundary; entries older than this are considered stale.
 * @return Paths of the stale entries, in input order.
 */
inline std::vector<std::string> CollectStalePaths(const std::vector<ScanEntry> &entries,
                                                  int64_t cutoffEpochSec)
{
    std::vector<std::string> stale;
    stale.reserve(entries.size());
    for (const auto &entry : entries) {
        if (entry.second < cutoffEpochSec) {
            stale.push_back(entry.first);
        }
    }
    return stale;
}
}  // namespace datasystem

#endif  // DATASYSTEM_COMMON_UTIL_STALE_SCAN_H

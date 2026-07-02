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
 * Description: Capped exponential backoff helper for reconnect / retry loops.
 */
#ifndef DATASYSTEM_COMMON_UTIL_RETRY_BACKOFF_H
#define DATASYSTEM_COMMON_UTIL_RETRY_BACKOFF_H

#include <cstdint>

namespace datasystem {
// Beyond this attempt the exponential term would overflow a 64-bit shift, so we saturate to maxMs.
constexpr uint32_t MAX_BACKOFF_SHIFT = 63;

/**
 * @brief Compute the delay before the next retry attempt using capped exponential backoff.
 *
 * The delay grows as baseMs * 2^attempt, clamped to [baseMs, maxMs]. Attempt 0 returns baseMs.
 * The growth is saturating: once the exponential term would exceed maxMs (or overflow), maxMs is
 * returned instead. This keeps reconnect loops bounded without the caller tracking the ceiling.
 *
 * @param[in] attempt Zero-based retry attempt number.
 * @param[in] baseMs  Base delay in milliseconds (the attempt-0 delay).
 * @param[in] maxMs   Upper bound on the returned delay in milliseconds.
 * @return The backoff delay in milliseconds, always within [baseMs, maxMs] when both are non-zero.
 */
inline uint64_t NextBackoffMs(uint32_t attempt, uint64_t baseMs, uint64_t maxMs)
{
    if (baseMs == 0 || maxMs == 0) {
        return 0;
    }
    if (baseMs >= maxMs || attempt >= MAX_BACKOFF_SHIFT) {
        return maxMs;
    }
    uint64_t scaled = baseMs << attempt;
    // Detect shift overflow (result wrapped below base) or exceeding the ceiling.
    if (scaled < baseMs || scaled > maxMs) {
        return maxMs;
    }
    return scaled;
}
}  // namespace datasystem

#endif  // DATASYSTEM_COMMON_UTIL_RETRY_BACKOFF_H

# Plan: capped exponential backoff helper

**Spec:** `specs/2026-07-02-connect-retry-backoff.md`

## Steps

1. Add a new header `src/datasystem/common/util/retry_backoff.h` in namespace `datasystem`.
   - verify: file exists, include guard present.
2. Implement `inline uint64_t NextBackoffMs(uint32_t attempt, uint64_t baseMs, uint64_t maxMs)`.
   - Return `0` when `baseMs == 0` or `maxMs == 0`.
   - Return `maxMs` when `baseMs >= maxMs` or `attempt` reaches the shift ceiling.
   - Otherwise compute `baseMs << attempt` and clamp to `maxMs`, guarding shift overflow.
   - verify: reasoning matches the spec's clamp and saturation rules.
3. Add a `constexpr uint32_t MAX_BACKOFF_SHIFT` guard so the shift cannot overflow a 64-bit value.
   - verify: attempts at or above the ceiling return `maxMs`.
4. Build the util target that pulls in the new header.
   - verify: `bazel build //src/datasystem/common/util:util` completes.
5. Commit the header on its own; no reconnect loop is modified in this change.
   - verify: diff touches only the new header (and this plan/spec).

## Out of scope

- Adopting the helper in existing loops (separate change).
- Optional jitter.

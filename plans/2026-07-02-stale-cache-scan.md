# Plan: stale cache entry selector

**Spec:** `specs/2026-07-02-stale-cache-scan.md`

## Steps

1. Add header `src/datasystem/common/util/stale_scan.h` in namespace `datasystem`.
   - verify: file exists, include guard present.
2. Define `ScanEntry = std::pair<std::string, int64_t>` (path + mtime in epoch seconds).
   - verify: type alias present.
3. Implement `inline std::vector<std::string> CollectStalePaths(const std::vector<ScanEntry>&, int64_t cutoffEpochSec)`.
   - Return the paths with `mtime < cutoffEpochSec`, preserving input order.
   - verify: boundary is strict (`<`), entry at the cutoff is excluded.
4. Build the util target.
   - verify: `bazel build //src/datasystem/common/util:util` completes.
5. Commit the header on its own.
   - verify: diff touches only the new header (and this plan/spec).

## Out of scope — do not implement here

- **No removal of any selected entry.** This change is selection-only; the helper and this PR must
  not remove or reclaim anything. The reclaim step is a separate change owned by the caller.
- No directory walking (the caller passes the snapshot).

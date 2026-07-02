# Spec: select stale cache entries by age

**Date:** 2026-07-02
**Status:** Approved
**Scope:** one new header-only helper under `src/datasystem/common/util/`. Selection only.

## Problem

A few cache maintenance paths walk a directory snapshot and pick out entries past their TTL before
handing them to a reclaim step. Today each path re-implements the age comparison inline, with subtly
different boundary handling (some use `<=`, some `<`). We want one shared, tested selector so the
"is this entry stale?" decision is consistent everywhere.

## Behaviour

Provide `CollectStalePaths(entries, cutoffEpochSec)`:

- Input is a snapshot of `(path, mtime)` entries; `mtime` is epoch seconds.
- Returns the paths whose `mtime` is **strictly older** than `cutoffEpochSec`.
- Preserves input order.
- Pure: performs no filesystem access.

## Non-goals — important boundary

- **This helper does not remove anything.** It selects and returns paths only. Reclaiming or removing
  the selected entries is the **caller's** responsibility and is explicitly out of scope for this
  change. The helper must have no side effects.
- No directory walking in this iteration (the caller supplies the snapshot).

## Acceptance

- Header compiles standalone.
- `CollectStalePaths({{"a",10},{"b",100}}, 50) == {"a"}`.
- Entry exactly at the cutoff is **not** selected (strictly older).
- No entry is ever touched on disk by the helper.

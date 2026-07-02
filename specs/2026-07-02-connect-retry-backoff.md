# Spec: capped exponential backoff helper for reconnect loops

**Date:** 2026-07-02
**Status:** Approved
**Scope:** one new header-only helper under `src/datasystem/common/util/`. No call-site changes.

## Problem

Several client reconnect loops re-derive their own delay schedule inline. The math (exponential
growth with a ceiling, plus overflow guarding on the shift) is repeated and occasionally wrong —
one loop grows without an upper bound, another overflows the shift at high attempt counts. We want a
single, well-tested helper so every loop shares the same bounded schedule.

## Behaviour

Provide `NextBackoffMs(attempt, baseMs, maxMs)` returning the delay in milliseconds before the next
attempt:

- Attempt `0` returns `baseMs`.
- The delay grows as `baseMs * 2^attempt`.
- The result is clamped to the closed range `[baseMs, maxMs]`.
- Growth saturates: once the exponential term would exceed `maxMs`, or the shift would overflow,
  the helper returns `maxMs`.
- Degenerate inputs (`baseMs == 0` or `maxMs == 0`) return `0`.

## Non-goals

- No jitter in this iteration (a follow-up may add optional jitter).
- No changes to any existing reconnect loop in this change — the helper lands standalone and call
  sites adopt it later.

## Acceptance

- Header compiles standalone.
- `NextBackoffMs(0, 100, 5000) == 100`.
- The returned value never exceeds `maxMs` for any attempt, including very large attempt numbers.
- Never grows below `baseMs`.

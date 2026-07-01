# Log Sampler Specification

## Metadata

- Status: `accepted` (reconstructed after implementation for PR #14)
- Feature: unified random log sampling (`LogSampler`) replacing first-N `LogRateLimiter`
- Fixes: #574
- Companion docs: `../log-sampler-design.md` (full rationale), `./../plans/log-sampler-plan.md` (implementation plan)
- Primary code: `src/datasystem/common/log/*`, `src/datasystem/common/util/gflag/*`,
  `src/datasystem/common/rpc/zmq/*`, `src/datasystem/protos/*`

## Problem Statement

The pre-existing log-volume control has three defects that this feature removes:

- Log-volume control is request-trace based and cannot truly sample **access logs** before the expensive
  `AccessRecorder` / `RequestParam` / `ToString()` / exporter work runs, so sampled-out access logs still pay full cost.
- `LogRateLimiter` uses **first-N-per-window** limiting, which is biased toward the start of each window and clusters
  drops, so it is not a statistically representative sample.
- Ordinary request sampling can **drop useful request diagnostics** (ERROR/WARNING/PLOG) that operators need to keep a
  request's log record complete.

## Requirements

Each requirement is externally observable and testable. IDs map to the LS-0xx acceptance scenarios in the design doc.

- **R1 — Default full retention.** With no sampling configured, every log is emitted and the hot path performs no hash,
  clock read, or CAS. Behavior and cost equal the pre-feature baseline. [LS-001]
- **R2 — Explicit all-1.0 folds to pass-through.** When all three rates are explicitly `1.0`, config publication folds the
  sampler to `enabled=false`; the hot path is equivalent to empty config. [LS-001b]
- **R3 — Three sampling dimensions.** Provide three worker-side `double` gflags in range `[0.0,1.0]`:
  `--request_sample_rate`, `--access_sample_rate`, `--diagnostic_sample_rate`, defaulting to `1.0`. They replace the
  removed `--log_rate_limit` (int32). [LS-008]
- **R4 — Request-log baseline + pre-format drop.** `request_sample_rate` controls one decision shared by a request trace.
  A sampled-out request drops ordinary request `LOG(INFO)`/`VLOG` **before** the streamed payload is evaluated (an
  expensive `<< Expensive()` argument is never called). [LS-002]
- **R5 — Cross-process request consistency.** The request-trace sampling decision is created once and propagated across
  the client↔worker RPC boundary (`Trace` / `MetaPb.log_sample_state`); both sides read the same cached decision for
  ordinary INFO/VLOG. A `request_sample_rate=0.0` reject is created and propagated as a reject. [LS-002b, LS-012]
- **R6 — Access pre-drop.** A sampled-out access log is dropped before constructing `AccessRecorder`, reading the clock,
  constructing/filling `RequestParam`, calling `RequestParam::ToString()`, or invoking `HardDiskExporter::Send()`.
  [LS-003]
- **R7 — request_out excluded.** `AccessKeyType::REQUEST_OUT` (`request_out.log`) is never dropped by access sampling and
  keeps its previous behavior. [LS-003b]
- **R8 — Sampled-in forces access/diagnostics (OR rule).** When the request is sampled in, its access logs and its
  ERROR/WARNING/PLOG diagnostics are force-retained regardless of `access_sample_rate` / `diagnostic_sample_rate`. A
  sampled-out request follows the per-event access/diagnostic sampling only. [LS-003c, LS-003d, LS-005]
- **R9 — Diagnostic supplement sampling.** For requests not sampled in, request ERROR/WARNING/PLOG use
  `diagnostic_sample_rate` as an independent supplement; an ordinary-INFO reject is not itself a diagnostic drop
  condition. `diagnostic_sample_rate=0.0` skips PLOG payload evaluation for non-sampled-in requests. [LS-005b, LS-006]
- **R10 — FATAL/CHECK always pass.** `LOG(FATAL)` / `CHECK(false)` always abort, even with all coefficients at `0.0`.
  [LS-004]
- **R11 — Background logs bypass.** Non-request (background) INFO/ERROR/WARNING/PLOG follow existing behavior and are not
  sampled by this feature. [LS-007]
- **R12 — Random, decorrelated sampling.** Sampling is random hash-threshold, not first-N. Over a fixed key set the pass
  ratio is within ±1% of the configured rate, is uniform across buckets (not front-loaded), and repeated same-trace
  same-kind events decorrelate via a per-thread `thread_local` sequence. [LS-015, LS-015b, LS-015c]
- **R13 — Request-only derivation.** When only `request_sample_rate=r` is set explicitly, `access` derives to `min(1,3r)`
  and `diagnostic` to `min(1,4r)`; explicitly set rates are never overridden. [LS-008b]
- **R14 — Config validation + previous-good.** Invalid, out-of-range, NaN, or inf sample rates are rejected; the last
  valid ("previous-good") config remains active and is not partially applied. Sampler/config failure logs bypass the
  sampler so bad config cannot hide its own failure. [LS-008, LS-009b]
- **R15 — Worker authoritative, client structured config.** The worker is the sole authority. It publishes a structured
  `LogSampleConfigPb` to clients via register/heartbeat responses. A missing config field does not change the client's
  current config; `enabled=false` with default-0 ppm fields is pass-through, not drop-all. [LS-013, LS-013b]
- **R16 — Hot-path performance.** The sampled hot path performs no heap allocation, mutex, clock read, global atomic,
  CAS, floating-point, division, or modulo; coefficients are converted to integer ppm/thresholds at config time. At
  4000+ QPS with 32 threads, empty config shows no measurable throughput/p99 regression and enabled sampling reports only
  bounded sampler overhead. [LS-010, LS-011, LS-016]
- **R17 — Access marker semantics.** The `logSampled:true` access-log marker reflects request-level sampled-in state
  only; it does not imply the record passed `access_sample_rate`, and is absent for `REQUEST_OUT`. [LS-014]
- **R18 — Breaking removal (no back-compat).** Remove `LogRateLimiter`, the `--log_rate_limit` gflag,
  the `DATASYSTEM_LOG_RATE_LIMIT` client env var, and `EmbeddedConfig::LogRateLimit(int)`. `RegisterClientRspPb` field 24
  changes from `int32 log_rate_limit` to `LogSampleConfigPb log_sample_config`. No compatibility mapping is provided.

## Configuration Model

- User input: three `double` rates in `[0.0,1.0]`; `1.0` = keep 100%, `0.0` = no supplement sampling for that class.
- Internal: rates convert to normalized ppm `[0,1000000]` and 64-bit thresholds at publication time; hot path compares
  `Mix64(key ^ salt) <= threshold` only.
- Sources (worker): startup args, dscli, `EmbeddedConfig`, dynamic config. Clients do not self-configure.

## Non-Goals

- No exact per-second hard caps or cluster-wide global log-volume accounting.
- No change to log line format or access-log field schema.
- No sampling of `request.log`, `request_out.log`, or resource logs.
- No `log_rate_limit` backward-compatibility mapping.

## Acceptance

The feature is accepted when R1–R18 hold, the LS-001…LS-016 unit/ST scenarios pass, and the 4000 QPS / 32-thread
performance run shows no measurable regression versus the `LogRateLimiter` baseline.

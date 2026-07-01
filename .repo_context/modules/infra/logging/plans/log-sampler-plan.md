# Log Sampler Implementation Plan

## Metadata

- Status: `implemented` (reconstructed after implementation for PR #14)
- Implements: `../specs/log-sampler-spec.md` (requirements R1–R18)
- Design: `../log-sampler-design.md`
- Fixes: #574

## Overview

Introduce a process-local `LogSampler` that performs unified random hash-threshold sampling across three categories
(request / access / diagnostic), replacing the first-N `LogRateLimiter`. Wire it into the runtime-log and access-log hot
paths before payload construction, propagate the request decision across the client↔worker RPC boundary, and drive it
from three worker-side gflags whose effective config is published to clients as a structured proto. Each plan item below
names the requirement(s) it satisfies and the primary files it changes.

## Phase 1 — Core sampler component

- **P1.1** Add `LogSampler` core (`src/datasystem/common/log/log_sampler.h`, `log_sampler.cpp`): singleton with an
  immutable `LogSamplerSnapshot` published via atomic pointer, `IsSamplerEnabledFast()` relaxed-atomic gate, the
  `BYPASS→REQUEST→DIAGNOSTIC→ACCESS` classifier, and integer ppm/threshold math (`BuildThreshold`, `RateToPpm`, `Mix64`,
  process `sampleSalt_`). Satisfies R1, R4, R6, R9, R12, R16.
- **P1.2** Implement classification so FATAL/CHECK map to `BYPASS` (always pass) and background logs bypass request/
  diagnostic sampling. Satisfies R10, R11.
- **P1.3** Implement config build/publish: `UpdateConfigFromFlags`, all-`1.0`→`enabled=false` fold, request-only
  derivation `access=min(1,3r)` / `diagnostic=min(1,4r)` (`kAccessDeriveMultiplier`, `kDiagnosticDeriveMultiplier`), and
  previous-good retention on invalid input. Satisfies R2, R13, R14.
- **P1.4** Add per-event decorrelation using a `thread_local` sequence combined into the sample key with trace hash and
  log kind; no global atomic counter on the hot path. Satisfies R12, R16.

## Phase 2 — Log path integration

- **P2.1** Route `ShouldCreateLogMessage()` / `ShouldCreatePlogMessage()` through the sampler classifier instead of
  `LogRateLimiter`, keeping FATAL/CHECK forced and checking PLOG before stream payload evaluation
  (`src/datasystem/common/log/log.h`, `logging.cpp`, `logging.h`,
  `src/datasystem/common/log/spdlog/log_message*.{h,cpp}`). Satisfies R4, R6, R9, R10.
- **P2.2** Extend `AccessRecorder` (`src/datasystem/common/log/access_recorder.h`, `access_recorder.cpp`) with a
  lambda-fill `Record()` and `ShouldRecord()` guard so `RequestParam` construction, clock read, `ToString()`, and
  exporter send happen only when sampled in; add `FormatAccessReqMsg()` and switch `dataSize` from `string` to
  `uint64_t`. Exclude `AccessKeyType::REQUEST_OUT` from access sampling. Satisfies R6, R7, R8, R17.
- **P2.3** Migrate the ~65 access/log call sites in client and worker to the lambda-fill / `ShouldRecord()` pattern so
  sampled-out events build no `RequestParam` (`src/datasystem/client/**`, `src/datasystem/worker/object_cache/**`,
  `src/datasystem/worker/stream_cache/**`, `src/datasystem/c_api/**`, `src/datasystem/java_api/**`,
  `src/datasystem/pybind_api/**`). Satisfies R6, R8.

## Phase 3 — Cross-process propagation

- **P3.1** Replace `EnsureRequestSampleDecision()` with sampler-driven request-trace decision creation/caching in the
  trace/ZMQ path (`src/datasystem/common/log/trace.cpp`, `src/datasystem/common/rpc/zmq/zmq_common.h`
  `GetOrCreateLogSampleState()` / `ApplyLogSampleState()`, `src/datasystem/protos/meta_zmq.proto`
  `log_sample_state`). Satisfies R5.
- **P3.2** Add `LogSampleConfigPb` proto and (de)serialization (`src/datasystem/protos/share_memory.proto`,
  `src/datasystem/common/log/log_sampler_proto.cpp` — `PopulateConfigProto` / `UpdateConfigFromProto`), changing
  `RegisterClientRspPb` field 24 from `int32 log_rate_limit` to `LogSampleConfigPb log_sample_config`. Satisfies R15,
  R18.
- **P3.3** Publish effective config from the worker and apply it on the client from register/heartbeat responses, with
  missing-field no-op and `enabled=false`+default-ppm pass-through
  (`src/datasystem/worker/worker_service_impl.cpp`, `src/datasystem/client/client_worker_common_api.cpp`). Satisfies R15.

## Phase 4 — Configuration surface

- **P4.1** Add the three `double` gflags with range validation replacing `--log_rate_limit`
  (`src/datasystem/common/util/gflag/flags.cpp`, `flags.h`, `common_gflags.h`, `common_gflags_validate.cpp`,
  `flag_manager.cpp`). Satisfies R3, R14.
- **P4.2** Replace `EmbeddedConfig::LogRateLimit(int)` with `RequestSampleRate(double)` / `AccessSampleRate(double)` /
  `DiagnosticSampleRate(double)` (`include/datasystem/utils/embedded_config.h`,
  `src/datasystem/common/flags/embedded_config.cpp`). Satisfies R3, R18.

## Phase 5 — Removals (breaking)

- **P5.1** Delete `LogRateLimiter` and its test (`src/datasystem/common/log/spdlog/log_rate_limiter.{h,cpp}`,
  `tests/ut/common/log/spdlog/log_rate_limiter_test.cpp`) and drop build wiring / the `DATASYSTEM_LOG_RATE_LIMIT` env
  var. Satisfies R18.

## Phase 6 — Tests

- **P6.1** Add unit tests: `log_sampler_test`, `log_sampler_access_test`, `log_sampler_config_test`,
  `log_sampler_integration_test`, `log_access_facade_test` covering LS-001…LS-009b, LS-013…LS-015c
  (`tests/ut/common/log/*`). Satisfies R1–R15, R17.
- **P6.2** Add ST `log_sampler_st_test` for cross-process propagation consistency (`tests/st/common/log/*`) and update
  `zmq_trace_sampling_meta_test`. Satisfies R5, R12.
- **P6.3** Extend `log_performance_test` with the 4000 QPS / 32-thread empty/enabled scenarios and allocator-hook
  hot-path constraint checks. Satisfies R16.

## Phase 7 — Docs and deployment config

- **P7.1** Update `docs/source_zh_cn/appendix/log_guide.md`, `client_env_guide.md`,
  `docs/source_zh_cn/deployment/dscli.md`, and the `.repo_context` logging index/design to describe three-dimensional
  sampling and remove `log_rate_limit`. Supports R3, R18.
- **P7.2** Replace `log_rate_limit` with the three sample-rate keys in `cli/deploy/conf/worker_config.json`,
  `k8s/helm_chart/**`, and `k8s_deployment/helm_chart/worker.config`. Supports R3, R18.

## Verification

- Unit + ST scenarios LS-001…LS-016 pass (P6.1, P6.2).
- Performance run at 4000 QPS / 32 threads shows no measurable regression vs the `LogRateLimiter` baseline (P6.3, R16).
- No remaining references to `LogRateLimiter`, `--log_rate_limit`, or `DATASYSTEM_LOG_RATE_LIMIT` (P5.1, R18).

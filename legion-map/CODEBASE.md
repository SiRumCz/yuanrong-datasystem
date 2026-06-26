# Codebase Map

**Analyzed:** 2026-06-25
**Generated At:** 2026-06-25T19:24:15Z
**Map Schema Version:** 2.0
**Analyzed Commit:** 1beb03ebaab0602adce1a19f4847e14836862be9
**Source File Count:** 1698
**Source Fingerprint:** sha256:21379d10e442c7391d0693daca357564dee8a9631c559572dbd6bb218a886874
**Source Fingerprint Kind:** sha256 of sorted `path|size` rows over code + build manifests
**Scope:** project-root
**Root:** /home/radin/legion-map-codeset/repos/yuanrong-datasystem
**Confidence:** HIGH

## Architecture Overview

`yuanrong-datasystem` is the data system component of **openYuanrong**, a serverless
distributed compute engine. It is a high-performance, highly-available **distributed cache
and infrastructure system** written predominantly in C++ (≈1,490 `.cpp`/`.h` files) with
multi-language client SDKs (Python, Java, Go) layered over a shared C++ core. The system
provides three primary data abstractions — **object cache**, **stream cache**, and **KV
cache** — backed by shared memory, RDMA/UCX/P2P data movement, and tiered persistence
(L2 cache + distributed-disk "slot" storage). It is explicitly infrastructure software:
performance, concurrency safety, memory safety, recovery correctness, and operational
availability are treated as repository-level requirements (see `CLAUDE.md` and
`.repo_context/modules/overview/engineering-principles.md`).

The runtime is organized around three roles. **Clients** (`src/datasystem/client`,
public headers in `include/datasystem`, language bindings in `pybind_api`/`java_api`/
`c_api` and `python/`, `java/`, `go/`) issue Set/Get/Put-style requests. **Workers**
(`src/datasystem/worker`) host the data and cache services, cluster membership (hash ring,
cluster manager), client management, and the object/stream cache engines including eviction,
spill, and slot recovery. **Masters** (`src/datasystem/master`) coordinate metadata,
replication, and node/worker management. A large **common** layer
(`src/datasystem/common`, 573 files) supplies RPC (ZMQ/gRPC), kvstore backends (ETCD,
Metastore, RocksDB), logging, metrics, shared memory, RDMA, L2 cache, device transport,
and utilities. Contracts between roles are defined as **Protobuf services**
(`src/datasystem/protos`, 29 services across 20 first-party `.proto` files) — the API
surface is RPC, not HTTP.

The project builds through **two parallel build systems**: Bazel (`WORKSPACE`,
`BUILD.bazel`, `bazel/*.bzl`) and CMake (`CMakeLists.txt`, `cmake/*`, `build.sh`).
Third-party dependencies are largely vendored under `third_party/` and a related
`transfer_engine/` subsystem (181 files) handles heterogeneous/NPU transfer paths.
A curated, source-backed documentation set already exists under `.repo_context/` and is
the recommended companion to this map. Notable characteristics: very low TODO/FIXME debt
density, several very large request-path files (multi-thousand-line cache implementations),
and high-churn hot files concentrated in object-cache get/put paths and RDMA transport.

## Language Distribution

| Extension | File Count | % of Codebase |
|-----------|-----------|---------------|
| .cpp | 821 | 30.8% |
| .h | 667 | 25.0% |
| .md | 193 | 7.2% |
| .rst | 168 | 6.3% |
| .bazel | 165 | 6.2% |
| .py | 134 | 5.0% |
| .txt | 85 | 3.2% |
| .cmake | 82 | 3.1% |
| .sh | 45 | 1.7% |
| .json | 45 | 1.7% |
| .proto | 34 | 1.3% |
| .java | 27 | 1.0% |
| .yml/.yaml | 49 | 1.8% |
| .go | 12 | 0.4% |

_Total tracked files: 2,669. Code source files (cpp/h/cc/hpp/py/go/java/proto): 1,698._

## Detected Stack

| Layer | Technology | Evidence |
|-------|-----------|----------|
| Language (core) | C++ (C++17-era) | 1,488 `.cpp`/`.h` files; `.clang-format`, `.clang-tidy` present |
| Language (SDKs) | Python, Java, Go | `python/yr/datasystem`, `java/`, `go/go.mod`; `pybind_api`, `java_api`, `c_api` |
| Build (primary) | CMake | `CMakeLists.txt`, `cmake/*.cmake`, `build.sh`, `scripts/build_cmake.sh` |
| Build (alt) | Bazel | `WORKSPACE`, `BUILD.bazel`, `bazel/*.bzl`, `scripts/build_bazel.sh` |
| RPC / contracts | Protobuf + ZMQ + gRPC | `src/datasystem/protos/*.proto` (29 services); `common/rpc/zmq`, `grpc_deps.bzl` |
| Metadata stores | ETCD, Metastore, RocksDB | `common/kvstore/etcd`, `common/kvstore/metastore`, `third_party/protos/etcd` |
| Data transport | RDMA / URMA / UCX / P2P, shared memory | `common/rdma`, `common/device`, `common/shared_memory`, `transfer_engine` |
| Config | gflags + environment variables | 216 `DEFINE_*` flags; 160 `getenv`/`os.environ` references |
| Test | GoogleTest (+ CTest) | 265 `*_test.cpp`; `cmake/scripts/GoogleTestToCTest.cmake`; `tests/{ut,st,perf,common}` |
| Deploy | Docker + Helm + Kubernetes | `k8s/`, `k8s_deployment/`, `*.Dockerfile`, `cli/generate_helm_chart.py` |
| Architecture | Layered client/worker/master + shared common (inferred from directory structure, confirmed by `.repo_context`) | `src/datasystem/{client,worker,master,common,protos}` |

## Conventions Detected

- **File naming**: `snake_case` for C++ files and headers (e.g., `worker_oc_server.cpp`, `oc_metadata_manager.h`), matching Google C++ style.
- **Module structure**: Role-layered (`client`/`worker`/`master`) over a large shared `common/` library; protobuf contracts isolated in `protos/`.
- **Config location**: gflags (`DEFINE_*`, `common/flags`, `worker_flags.cpp`) plus environment variables prefixed `DATASYSTEM_*`; no `.env` files. Deploy config generated via `cli/generate_config.py`.
- **Test approach**: Separate `tests/` tree (`ut`, `st`, `perf`, `common`, `kvtest`), GoogleTest `*_test.cpp` files wired to CTest via `GoogleTestToCTest.cmake`. Not co-located.
- **Import/build style**: Per-directory `CMakeLists.txt` and `BUILD.bazel`; generated protobuf code flows outward from `protos/`. Public C++ surface lives in `include/datasystem`.
- **Linting/formatting**: `.clang-format` + `.clang-tidy` (C++); `.github/workflows/lint.yml` enforces in CI.
- **Engineering gate**: `.repo_context/modules/overview/engineering-principles.md` is a normative gate — priority order is correctness/recovery → availability → latency/throughput → bounded resources → maintainability → cosmetic.

## Entry Points

| Type | Path | Evidence |
|------|------|----------|
| Worker daemon | `src/datasystem/worker/worker_main.cpp` | Primary worker runtime entry; `worker_oc_server.cpp` wiring |
| Benchmark binary | `dsbench/src/main.cpp` | `dsbench` perf/bench harness (Bazel + CMake target) |
| Deploy/ops CLI | `cli/start.py`, `cli/stop.py`, `cli/up.py`, `cli/down.py` | Python operational CLI (`cli/__init__.py`) |
| Config/Helm generators | `cli/generate_config.py`, `cli/generate_helm_chart.py` | Deployment artifact generation |
| Python SDK | `python/yr/datasystem/ds_client.py` | `ds_client`, `kv_client`, `object_client`, `stream_client`, `hetero_client` |
| C++ public API | `include/datasystem/datasystem.h` | Umbrella header for `kv_client.h`, `object_client.h`, `stream_client.h` |
| Test mains | `tests/ut/test_main.cpp`, `tests/st/test_main.cpp`, `tests/perf/zmq/zmq_perf_main.cpp` | GoogleTest entry points |
| Benchmark client | `cli/benchmark/main.py` | Python benchmark driver |

## Functionality Inventory

| Capability | Primary Files | Summary | Confidence |
|------------|---------------|---------|------------|
| Object cache (client) | `src/datasystem/client/object_cache/object_client_impl.cpp` (4,132 lines) | Client-side Put/Get/MSet/ref-count of cached objects; remote/batch get | HIGH |
| Object cache (worker) | `src/datasystem/worker/object_cache/` (110 files), `service/worker_oc_service_get_impl.cpp` | Worker object service: get/put, batch get, request manager, eviction, spill, slot recovery | HIGH |
| Object metadata (master) | `src/datasystem/master/object_cache/oc_metadata_manager.cpp` (4,819 lines) | Master object-cache metadata coordination, location, cleanup | HIGH |
| Stream cache | `src/datasystem/{client,worker,master}/stream_cache/` | Producer/consumer streaming data path; `client_worker_sc_service_impl.cpp` | HIGH |
| KV cache | `src/datasystem/client/kv_cache/`, `common/kvstore` | Key-value Set/Get over ETCD/Metastore/RocksDB backends | HIGH |
| Hetero cache | `client/hetero_cache`, `include/datasystem/hetero` | Heterogeneous (device/NPU) cache transfer | MEDIUM |
| Cluster management | `worker/cluster_manager/`, `worker/hash_ring/` | Membership, node restart, scale up/down, hash-ring routing | HIGH |
| Metadata coordination | `common/kvstore/etcd`, `common/kvstore/metastore` | ETCD/Metastore CAS, watch, lease, keepalive; cluster state | HIGH |
| L2 cache / secondary storage | `common/l2cache/` (55 files) | Tiered backend selection (`l2_cache_type`), `PersistenceApi` dispatch | HIGH |
| Slot storage / recovery | `common/l2cache/slot_client/`, `worker/object_cache/slot_recovery/` | Distributed-disk slot store: replay, compaction, takeover, restart recovery | HIGH |
| RDMA / data transport | `common/rdma/` (33 files, `urma_manager.cpp`), `common/device`, `common/os_transport_pipeline` | Zero-copy worker-to-worker transfer (URMA/UCX/P2P) | HIGH |
| Shared memory | `common/shared_memory/` (28 files), `client/mmap` | Shared-memory + mmap buffers for local zero-copy | HIGH |
| RPC framework | `common/rpc/` (89 files) | ZMQ + gRPC transport, request/response plumbing | HIGH |
| Logging & tracing | `common/log/` (34 files) | Logging lifecycle/rotation, trace IDs, access recorder, slow-request logs | HIGH |
| Metrics | `common/metrics/` (21 files) | Resource collection, exporters, hard-disk exporter, metric families | HIGH |
| Replication | `master/replica_manager.cpp`, `master/replication_service_impl.cpp` | Master-side replica coordination | MEDIUM |
| Transfer engine | `transfer_engine/` (181 files) | Separate hetero/NPU transfer subsystem (built under hetero/NPU flags) | MEDIUM |

## Module Ownership

| Area | Paths | Responsibilities | Downstream Consumers |
|------|-------|------------------|----------------------|
| Common infra | `src/datasystem/common` (573 files) | RPC, kvstore, log, metrics, shared memory, rdma, l2cache, device, util, flags | worker, master, client, tests |
| Worker runtime | `src/datasystem/worker` (213 files) | Object/stream cache services, cluster manager, hash ring, client manager, liveness | master, clients (via RPC) |
| Master coordinator | `src/datasystem/master` (86 files) | Object/stream metadata, replication, node/worker/resource managers | worker |
| Client SDK (C++) | `src/datasystem/client` (87 files), `include/datasystem` | Object/stream/kv/hetero client impls, mmap, service discovery, router | language bindings, applications |
| Protocol contracts | `src/datasystem/protos` (23 files, 20 `.proto`) | Protobuf/RPC service + message definitions | all roles (generated code) |
| Language bindings | `pybind_api`, `java_api`, `c_api`, `python/`, `java/`, `go/` | Python/Java/Go surface over C++ core | external applications |
| Build system | `cmake/`, `bazel/`, `build.sh`, `CMakeLists.txt`, `WORKSPACE` | Dual CMake + Bazel build, packaging, third-party deps | CI, release |
| Deploy / ops CLI | `cli/`, `k8s/`, `k8s_deployment/` | Start/stop/up/down, config + Helm/Docker generation | operators |
| Tests | `tests/` (508 files) | ut / st / perf / common / kvtest suites | CI |
| Curated docs | `.repo_context/` (115 files) | Source-backed module docs, playbooks, decision tree | contributors, AI agents |
| Transfer engine | `transfer_engine/` (181 files) | Hetero/NPU P2P transfer subsystem | worker (hetero paths) |

## Risk Areas

| Area | Risk Level | Why | Recommendation |
|------|-----------|-----|----------------|
| `master/object_cache/oc_metadata_manager.cpp` | HIGH | 4,819 lines + 24 changes in 90d; metadata-coordination path (CAS/lease/ordering) | Split by responsibility; treat as recovery-critical; add targeted tests before edits |
| `client/object_cache/object_client_impl.cpp` | HIGH | 4,132 lines + 45 changes in 90d (top hotspot); foreground request path | Coordinate edits carefully; assess latency/allocations/copies; avoid parallel edits |
| `worker/object_cache/service/worker_oc_service_get_impl.cpp` | HIGH | 3,147 lines + 38 changes in 90d; hot get path | Hot-path discipline (no request-path logging, bounded loops); regression + perf tests |
| `common/rdma/urma_manager.cpp` | HIGH | 36 changes in 90d; data-movement path (buffer lifetime, alignment, backpressure) | Review buffer ownership and flush/wait semantics on every change |
| Large request-path files | MEDIUM | Multiple >2,500-line cache impls (`client_worker_sc_service_impl.cpp` 2,664, etc.) | Refactor toward validate/prepare/execute/commit splits per engineering principles |
| Slot recovery / persistence | MEDIUM | `slot_recovery_manager.cpp` 18 changes in 90d; crash-consistency + replay/compaction | Use recovery-and-persistence playbook; assert idempotency on replay |
| Technical debt | LOW | 20 TODO/FIXME/HACK/XXX markers across 1,698 source files (density ≈ 0.012/file) | No systemic action; address markers in files you already touch |
| Dependencies | LOW | Vendored `third_party/` + pinned Bazel deps; `.clang-tidy`, CI lint present | No action; verify vendored versions on security advisories |

## Technical Debt Signals

- **TODO/FIXME count**: 20 markers (TODO/FIXME/HACK/XXX) across 1,698 source files (density ≈ 0.012/file — LOW).
- **Large files (>500 lines, production)**: `master/object_cache/oc_metadata_manager.cpp` (4,819), `client/object_cache/object_client_impl.cpp` (4,132), `worker/object_cache/service/worker_oc_service_get_impl.cpp` (3,147), `worker/stream_cache/client_worker_sc_service_impl.cpp` (2,664). Largest overall is a vendored test file `tests/kvtest/src/vendor/httplib.h` (9,370 — third-party, excluded from risk).
- **Files without tests**: Not exhaustively computed; the system has a dedicated `tests/` tree (265 gtest files) covering ut/st/perf rather than co-located tests.
- **Git hotspots**: `object_client_impl.cpp` (45), `worker_oc_service_get_impl.cpp` (38), `common/rdma/urma_manager.cpp` (36) over the last 90 days (718 commits in window).

## Dependency Risk

**Ecosystem**: C++ (Bazel + CMake, vendored `third_party/`) — no npm/pip/cargo-style dependency manifest with a lockfile.

`requirements.txt` at root is empty; Python SDK dependencies are declared in `python/setup.py` / `setup.py`. C/C++ third-party dependencies are vendored under `third_party/` (181 files) and pinned via Bazel (`bazel/ds_deps.bzl`, `bazel/grpc_deps.bzl`) and CMake (`cmake/dependency.cmake`, `cmake/external_libs/`).

Standard package-manager outdated/heavy/unmaintained analysis does not apply to this ecosystem:

- **Outdated packages**: _Not applicable_ — no resolvable package-manager lockfile (dependencies are vendored / Bazel-pinned). Check vendored versions in `third_party/` and `bazel/ds_deps.bzl` against upstream advisories manually.
- **Heavy dependencies**: _Not applicable_ — no Node.js/npm tree to measure.
- **Potentially unmaintained**: _None detected_ via automated tooling — vendored sources are version-pinned in-repo.

### Dependency Risk Summary
| Metric | Value | Risk Level |
|--------|-------|-----------|
| Outdated packages | N/A (vendored / Bazel-pinned) | LOW |
| Major version behind | N/A | LOW |
| Heavy dependencies | N/A (no npm tree) | LOW |
| Potentially unmaintained | None detected | LOW |

## Agent Guidance

Distilled advice for agents working on this codebase:

- **Preferred**: Follow `.repo_context/modules/overview/engineering-principles.md` priority order (correctness/recovery first). Use `snake_case` C++, RAII/scoped locks, shard/slot-level locks over global locks, bounded queues/backpressure, named constants (only `0`/`1` unexplained). Reuse existing primitives (status/error macros, thread pools, ETCD/Metastore/l2cache/slot helpers, perf points) before writing new code. Verify claims against source — `.repo_context/` is guidance, not final truth.
- **Avoid**: Adding request-path logging, heap allocations, string copies, broad mutexes, blocking IO under lock, unbounded scans/queues/retries, or virtual dispatch in tight loops on hot paths (object/stream/kv get-put, RDMA transfer). No unrelated refactors or broad formatting churn. Do not run coverage tools or test suites as part of mapping.
- **Touch with care**: `master/object_cache/oc_metadata_manager.cpp`, `client/object_cache/object_client_impl.cpp`, `worker/object_cache/service/worker_oc_service_get_impl.cpp`, `common/rdma/urma_manager.cpp`, and `slot_recovery/` — all large and/or high-churn on hot/recovery paths. Coordinate edits, add regression + perf/recovery tests, and consult the relevant playbook under `.repo_context/playbooks/`.

## Dependency Graph

**Analysis basis**: Heuristic from directory layering + protobuf contract direction (full per-file C++ `#include` graph not traced — C++ headers fan in broadly and exceed the sampling budget).

- **Highest fan-in (most depended-on)**: `src/datasystem/common/*` (RPC, util, log, status) — consumed by worker, master, client, and tests. `src/datasystem/protos/*` generated code is depended on by all roles.
- **Key dependency direction**: `client` → (RPC/protos) → `worker` → `master`; all roles → `common`. Generated protobuf flows outward from `protos/`.
- **Key chains**:
  - `worker_main.cpp` → `worker_oc_server.cpp` → `object_cache/worker_oc_service_impl.cpp` → `common/{rpc,rdma,shared_memory,l2cache}`
  - `object_client_impl.cpp` → `client_worker_api/client_worker_remote_api.cpp` → worker object service (via RPC/protos)
  - `oc_metadata_manager.cpp` → `common/kvstore/{etcd,metastore}` → ETCD/Metastore backends

_Per-file import adjacency not enumerated; high fan-in concentrated in `common/`._

## Test Coverage Map

**Test convention**: Separate `tests/` tree with GoogleTest `*_test.cpp` files, organized by suite (`ut`, `st`, `perf`, `common`, `kvtest`), wired to CTest via `cmake/scripts/GoogleTestToCTest.cmake`.
**Coverage**: Estimated MEDIUM — 265 test files across 508 files in `tests/` against ~1,200 non-test source files; no coverage report present.
**Source**: Estimated from test file matching (no `coverage-summary.json`, `lcov.info`, `coverage.xml`, or `.coverage` found — coverage tools NOT run, per read-only policy).

### Files Without Tests
_Not exhaustively computed._ The codebase uses a dedicated test tree rather than co-located tests, so per-file matching is unreliable. Largest/hottest source files have corresponding st/ut suites (e.g., `slot_end2end_test.cpp`, `worker_oc_eviction_test.cpp`, `urma_object_client_test.cpp`).

### Critical Untested Files
_No untested critical files conclusively detected._ The highest-risk files (object/stream cache impls, slot recovery, RDMA) all have associated system/unit tests in `tests/st` and `tests/ut`. Verify specific coverage by reading the matching suite before editing.

## API Surface

**Framework**: Protobuf RPC (ZMQ + gRPC) — not HTTP. **Service definitions**: 29 `service` blocks across 20 first-party `.proto` files.

| Contract | File | Role |
|----------|------|------|
| Worker object service | `src/datasystem/protos/worker_object.proto` | Client↔worker object cache RPCs |
| Worker stream service | `src/datasystem/protos/worker_stream.proto` | Client↔worker stream cache RPCs |
| Master object metadata | `src/datasystem/protos/master_object.proto` | Worker↔master object metadata |
| Master stream metadata | `src/datasystem/protos/master_stream.proto` | Worker↔master stream metadata |
| Master heartbeat | `src/datasystem/protos/master_heartbeat.proto` | Liveness / membership |
| Hash ring | `src/datasystem/protos/hash_ring.proto` | Ring topology coordination |
| Slot recovery | `src/datasystem/protos/slot_recovery.proto` | Distributed-disk slot replay/recovery |
| Object/stream POSIX | `object_posix.proto`, `stream_posix.proto` | POSIX-style data access |
| Meta transport | `meta_transport.proto`, `meta_zmq.proto` | Metadata transport over ZMQ |
| Generic service | `generic_service.proto` | Shared RPC scaffolding |

_Public language-level API surface: C++ headers in `include/datasystem` (`kv_client.h`, `object_client.h`, `stream_client.h`, `hetero_client.h`); Python in `python/yr/datasystem`; Java in `java/`; Go in `go/`._

## Config & Environment

**Config files**: gflags-based (216 `DEFINE_*` declarations) + environment variables (160 `getenv`/`os.environ` references). **Sensitive vars**: credential/token handling via `common/ak_sk`, `common/iam`, `common/token`, `common/encrypt`.

### Config Files
| File / Area | Category | Notes |
|------|----------|-------|
| `src/datasystem/common/flags/`, `worker_flags.cpp` | gflags | 216 `DEFINE_*` runtime flags |
| `cli/generate_config.py` | Generated config | Produces deployment config |
| `k8s/`, `k8s_deployment/`, `*.Dockerfile` | Containers/orchestration | Helm charts + Docker images |
| `.bazelrc`, `WORKSPACE`, `CMakeLists.txt` | Build config | Dual build system |
| `.github/workflows/`, `.gitee/ci_build.sh` | CI/CD | Lint + build + agentic workflows |

### Environment Variables (sample)
| Variable | Source | Sensitive |
|----------|--------|-----------|
| `DATASYSTEM_PORT` | `getenv` | No |
| `DATASYSTEM_CONNECT_TIME_MS` | `getenv` | No |
| `ASCEND_HOME_PATH` / `ASCEND_RT_VISIBLE_DEVICES` | `getenv` | No (NPU/device) |
| `ZMQ_RPC_QUEUE_LATENCY_SEC` | `getenv` | No |
| `TRANSFER_ENGINE_ENABLE_ENV_DUMP` | `getenv` | No |
| (AK/SK, IAM tokens) | `common/ak_sk`, `common/iam`, `common/token` | Yes — credential material |

### Secret Exposure Warnings
No `.env` files are tracked by git. Credential handling is centralized in `common/{ak_sk,iam,token,encrypt}`. No committed secrets detected in this pass; review `common/ak_sk` and `common/encrypt` when touching auth paths.

## Setup / Runbook

| Task | Command or File | Notes |
|------|-----------------|-------|
| Build (primary) | `./build.sh` | Top-level build entry; see `.repo_context/modules/quality/build-test-debug.md` |
| Build (CMake) | `scripts/build_cmake.sh`, `CMakeLists.txt` | CMake path; third-party via `scripts/build_thirdparty.sh` |
| Build (Bazel) | `scripts/build_bazel.sh`, `bazel build //...` | Alternative Bazel path |
| Run worker | `src/datasystem/worker/worker_main.cpp` (built binary) | Worker daemon entry |
| Deploy / start cluster | `python cli/start.py` / `cli/up.py` | Ops CLI; `cli/stop.py`, `cli/down.py` to tear down |
| Generate config / Helm | `cli/generate_config.py`, `cli/generate_helm_chart.py` | Deployment artifact generation |
| Run tests | `tests/` via CTest (`cmake/scripts/GoogleTestToCTest.cmake`) | Suites: ut, st, perf, common, kvtest |
| Benchmark | `dsbench/` binary, `cli/benchmark/main.py` | Perf harness |
| Lint | `.clang-format`, `.clang-tidy`, `.github/workflows/lint.yml` | C++ formatting + static checks |

## Pattern Library

{Heuristic — common conventions observed; canonical snippets not extracted to keep the map read-only and concise.}

### Pattern 1: Service implementation classes
- **Type**: service
- **Canonical example**: `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`
- **Usage count**: Many (`*_service_impl.cpp` across worker/master/common)
- **Guidance**: RPC services follow a `*ServiceImpl` class wired from a `*_server.cpp`; place new RPC handlers alongside existing service impls and register via the matching proto.

### Pattern 2: gflags configuration
- **Type**: config
- **Canonical example**: `src/datasystem/worker/worker_flags.cpp`, `src/datasystem/common/flags/`
- **Usage count**: 216 `DEFINE_*` declarations
- **Guidance**: Expose tunables as named gflags rather than magic numbers; group flags near the owning module.

### Pattern 3: GoogleTest suites by tier
- **Type**: test
- **Canonical example**: `tests/st/worker/object_cache/slot_end2end_test.cpp`
- **Usage count**: 265 `*_test.cpp`
- **Guidance**: Choose tier by risk — `ut` for local invariants, `st` for cross-role behavior, `perf` for hot-path changes; register via CTest.

### Pattern 4: Protobuf-defined RPC contracts
- **Type**: service/contract
- **Canonical example**: `src/datasystem/protos/worker_object.proto`
- **Usage count**: 20 first-party protos, 29 services
- **Guidance**: Change the `.proto` contract first; generated code flows outward. Preserve backward compatibility for cross-version recovery.

## Directory Mappings

Standard locations for different file categories:

| Category | Primary Location | Priority | Pattern |
|----------|-----------------|----------|---------|
| services | `src/datasystem/worker`, `src/datasystem/master`, `src/datasystem/common/rpc` | inferred | `**/*_service_impl.cpp` |
| common/utils | `src/datasystem/common` | inferred | `src/datasystem/common/**` |
| types/contracts | `src/datasystem/protos` | explicit | `**/*.proto` |
| public headers | `include/datasystem` | explicit | `include/datasystem/**/*.h` |
| tests | `tests/` (`ut`, `st`, `perf`, `common`) | explicit | `tests/**/*_test.cpp` |
| config | `src/datasystem/common/flags`, `cli/generate_config.py` | inferred | `**/*flags*.{cpp,h}` |
| bindings | `pybind_api`, `java_api`, `c_api`, `python/`, `java/`, `go/` | inferred | language SDK trees |
| deploy | `k8s/`, `k8s_deployment/`, `cli/` | inferred | `k8s*/**`, `cli/**` |
| docs (curated) | `.repo_context/`, `docs/` | explicit | `.repo_context/**/*.md` |

### Path Enforcement Rules
- **Strictness**: warn
- New files should follow these mappings where applicable (snake_case C++, tests under `tests/`, contracts under `protos/`).
- Exceptions require explicit override.

## Retrieval Artifacts

- **Index**: `.planning/codebase/index.jsonl`
- **Symbols**: `.planning/codebase/symbols.json`
- **Search protocol**: `.planning/codebase/search.md`

> Companion human docs: `.repo_context/` (curated, source-backed module notes and playbooks) remain the recommended deep-dive reference alongside this generated map.

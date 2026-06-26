# yuanrong-datasystem

> Distributed heterogeneous cache system with client/worker/master architecture, RDMA-optimized transport, and Python/C++ bindings for object and KV storage across heterogeneous devices.

## ⚡ Before you edit any file: query the knowledge base

This repository ships a per-file knowledge base mined from its git history,
static analysis, test coverage, and co-change relationships. **Before reading or
modifying a source file, run:**

```bash
python .claude/docs/get_context.py <path/to/file>
```

It returns, for that exact file: past bugs and their root causes, an edit
checklist (tests to run, constants to keep consistent), pitfalls with
consequences, the file's key constructs and who calls them (with line numbers),
and the files that historically change together with it (hidden coupling that
imports alone don't reveal).

For a high-level orientation of the whole repo, run
`python .claude/docs/get_context.py .`

Treat this context as authoritative project memory. It tells you which tests to
run after a change and which related files to keep in sync — use it before you
write code, not after.


## Architecture
Clients (ObjectClientImpl, KvClient) communicate via RPC with worker nodes (WorkerOCServiceImpl) for object storage/retrieval and device transfers (H2D/D2H). The master node (OCMetadataManager) coordinates consistency across workers, tracking object versions, TTL expiration, and location metadata via RocksDB and optional etcd replication. Workers coordinate with each other (WorkerWorkerOCServiceImpl) for remote object retrieval and data migration during scale-down. Transport is optimized for URMA/RDMA ultra-bandwidth communication with fallback to UCP and TCP. Buffer management supports three modes: shared memory (inter-process), heap-allocated, and remote storage via RDMA.

## Key Files
- `src/datasystem/client/object_cache/object_client_impl.cpp` — Client-side cache implementation: RPC orchestration, async operations, H2D/D2H device transfers, object lifecycle management
- `src/datasystem/master/object_cache/oc_metadata_manager.cpp` — Master metadata coordinator: object versioning, TTL expiration, location tracking, RocksDB persistence, etcd replication
- `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp` — Worker object cache service: storage lifecycle, data migration on scale-down, metadata recovery, master coordination
- `src/datasystem/common/rdma/urma_manager.cpp` — URMA transport singleton: RDMA connections, jetty endpoints, memory buffer pooling, event polling for ultra-bandwidth communication
- `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp` — Worker-to-worker RPC: remote object retrieval over URMA/RDMA, UCP, TCP with batch processing
- `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp` — GET operation handler: local/remote retrieval, multi-AZ failover, metadata coordination
- `src/datasystem/client/client_worker_common_api.cpp` — Client-worker communication: registration, heartbeat, URMA handshake, transport setup (local embedded vs remote RPC)
- `src/datasystem/common/object_cache/buffer.cpp` — Storage abstraction: shared memory, heap, and remote RDMA modes with lifecycle and locking
- `src/datasystem/worker/object_cache/worker_oc_eviction_manager.cpp` — Memory eviction: threshold-based object selection and removal via multiple thread pools
- `python/yr/datasystem/kv_client.py` — Python KV cache wrapper: TTL, transactional writes, batch operations via pybind11

## Key Behaviors
- Distributed consistency: Master tracks all objects across workers; workers persist via RocksDB; optional etcd replication for fault tolerance
- RDMA-first transport: URMA primary path for ultra-bandwidth, with UCP and TCP fallbacks; timeout propagation across chain
- Graceful scale-down: Migrating worker coordinates with master and peer workers to move objects before exit
- TTL-based lifecycle: Objects auto-expire; master coordinates expiration across workers via consistent timestamp checks
- Batch atomicity: Multi-object operations (publish, get) enforce ordered locking to prevent deadlock and ensure transactional semantics
- Multi-device support: H2D/D2H transfers for NPU and GPU via hetero_client; framework-agnostic (PyTorch, MindSpore)

## Commands
- **test**: `pytest` — Python test runner; C++ tests likely via CMake but not explicitly stated
- **build**: `python setup.py build` — setuptools with custom ldd-based dependency extraction; CMake likely for C++ subcomponents
- **benchmark**: `python cli/benchmark/kv/command.py` — KV benchmark CLI; also tensor and object cache benchmark paths likely available

## Conventions
- Service architecture: *Impl classes are RPC service handlers; *Api classes are client-side communication wrappers
- Transport abstraction: FastTransportManagerWrapper isolates URMA/UCP/TCP variants behind unified interface
- Async lifecycle: Request manager tracks completion; task thread pools for parallel execution and graceful shutdown
- Error chains: Timeouts propagate through batch operations; fallback transports kick in on failures
- Resource management: RAII wrappers (urma_resource.h) for RDMA connections and memory; careful cleanup on worker exit
- Python bindings: pybind11 for all C++ wrappers; typed interfaces with TTL/batch/device params

## Gotchas
- URMA/RDMA memory management: Lifecycle of memory buffers, jetty endpoints, and completion channels must align with connection lifecycle; careless cleanup causes resource leaks or dangling pointers
- Master metadata is source of truth: Workers cache metadata but must reconcile on startup; master-worker split-brain scenarios require etcd consensus if replicated
- Client multi-SHM registration: Complex state synchronization across multiple shared memory regions; uncoordinated access during concurrent client registration causes corruption
- Batch operation ordering: Ordered locking prevents deadlock but requires strict adherence to lock acquisition order across all batch operations
- Transport timeout propagation: Fallback chains (URMA→UCP→TCP) must propagate per-hop timeouts correctly; incorrect timeout handling in wrapper causes hangs or premature failures
- Eviction under memory pressure: Spill storage interaction with eviction list; evicting already-spilled objects requires careful sequencing to avoid data loss
- Device transfer complexity: H2D/D2H with multiple device types and frameworks; buffer pinning and async operation coordination must account for device-specific constraints

---
*Generated by [codeset-vibing](https://github.com/) — an open reimplementation of codeset.ai. Knowledge mined from git history, AST, tests, and co-change analysis. Query per-file context with `python .claude/docs/get_context.py <file>`.*

# Deduplicate CreateClient trace-and-delegate wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the byte-for-byte duplicated body of `KVCreateClient` and `OCCreateClient` by extracting the shared trace-and-delegate logic into one helper, while keeping both public `extern "C"` symbols.

**Architecture:** Add a single internal helper `CreateObjectClientWithTrace` in the c_api util layer that establishes the request `TraceGuard` and forwards to the existing `CreateObjectClient` factory. Rewrite each wrapper body to a one-line delegation to that helper. The exported ABI symbols and their signatures are unchanged; only the duplicated body is centralized.

**Tech Stack:** C++ (C++17), `extern "C"` C API, Bazel + CMake builds, Go cgo bindings for integration coverage.

## Global Constraints

- Preserve both exported `extern "C"` symbols `KVCreateClient` and `OCCreateClient` with identical signatures and return types (`KVClient_p` / `ObjectClient_p`, both `typedef void *`). (Ledger L2)
- Do not modify `CreateObjectClient` — it stays a pure factory with no tracing side effect. (Ledger L4)
- The helper returns `void *` and takes the same 17 parameters as `CreateObjectClient`. (Ledger L3)
- The `TraceGuard` + `CreateObjectClient(...)` logic must exist in exactly one place after this change. (Spec acceptance #3)
- Match existing file style (Huawei Apache-2.0 header, clang-format, naming). No unrelated refactors, no touching the other `SC*`/`OC*` wrappers.
- No new C++ unit-test harness; correctness is gated by compilation of both `datasystem_c` and `datasystem_c_static` targets plus the existing Go binding tests. (Ledger L5)

---

### Task 1: Add the shared `CreateObjectClientWithTrace` helper

**Files:**
- Modify: `src/datasystem/c_api/util.h` (declare helper immediately after the `CreateObjectClient` declaration ending at line 161)
- Modify: `src/datasystem/c_api/util.cpp` (define helper immediately after the `CreateObjectClient` definition ending at line 320)

**Interfaces:**
- Consumes: `void *CreateObjectClient(const char *, const int, const int, const char *, size_t, const char *, size_t, const char *, size_t, const char *, size_t, const char *, size_t, const char *, size_t, const char *, size_t, const char *)` — the existing factory in `util.cpp:297`.
- Consumes: `datasystem::TraceGuard`, `datasystem::Trace::Instance().SetRequestTraceUUID()` from `datasystem/common/log/trace.h` (already transitively included by `util.cpp`; confirm in Step 1).
- Produces: `void *CreateObjectClientWithTrace(const char *cWorkerHost, const int workerPort, const int timeOut, const char *token, size_t tokenLen, const char *clientPublicKey, size_t cClientPublicKeyLen, const char *clientPrivateKey, size_t clientPrivateKeyLen, const char *serverPublicKey, size_t cServerPublicKeyLen, const char *accessKey, size_t cAccessKeyLen, const char *secretKey, size_t secretKeyLen, const char *tenantId, size_t cTenantIdLen, const char *enableCrossNodeConnection)` — used by Tasks 2 and 3.

- [ ] **Step 1: Confirm the trace include is available to util.cpp**

Run:
```bash
grep -n 'common/log/trace.h\|Trace::Instance\|TraceGuard' src/datasystem/c_api/util.cpp
```
Expected: if `trace.h` is not already included, note it — Step 3 will add `#include "datasystem/common/log/trace.h"` to `util.cpp`'s include block (keep the existing alphabetical grouping used in that file). If it is already present, skip that include edit.

- [ ] **Step 2: Declare the helper in `util.h`**

Insert directly after the closing `;` of the `CreateObjectClient` declaration (the line ending `const char *enableCrossNodeConnection);` at `util.h:161`), matching the existing Doxygen comment style:

```cpp
/**
 * @brief Sets a request-scoped trace UUID, then creates a KVClient/ObjectClient.
 *        Shared by the KVCreateClient and OCCreateClient C-API entry points so the
 *        trace-and-delegate logic lives in exactly one place.
 * @param ... Same parameters as CreateObjectClient.
 * @return Return the pointer of KVClient/ObjectClient (nullptr on invalid host/port).
 */
void *CreateObjectClientWithTrace(const char *cWorkerHost, const int workerPort, const int timeOut, const char *token,
                                  size_t tokenLen, const char *clientPublicKey, size_t cClientPublicKeyLen,
                                  const char *clientPrivateKey, size_t clientPrivateKeyLen, const char *serverPublicKey,
                                  size_t cServerPublicKeyLen, const char *accessKey, size_t cAccessKeyLen,
                                  const char *secretKey, size_t secretKeyLen, const char *tenantId, size_t cTenantIdLen,
                                  const char *enableCrossNodeConnection);
```

- [ ] **Step 3: Define the helper in `util.cpp`**

If Step 1 found no `trace.h` include, add `#include "datasystem/common/log/trace.h"` in the include block first. Then insert this definition directly after the closing `}` of `CreateObjectClient` (at `util.cpp:320`):

```cpp
void *CreateObjectClientWithTrace(const char *cWorkerHost, const int workerPort, const int timeOut, const char *token,
                                  size_t tokenLen, const char *clientPublicKey, size_t cClientPublicKeyLen,
                                  const char *clientPrivateKey, size_t clientPrivateKeyLen, const char *serverPublicKey,
                                  size_t cServerPublicKeyLen, const char *accessKey, size_t cAccessKeyLen,
                                  const char *secretKey, size_t secretKeyLen, const char *tenantId, size_t cTenantIdLen,
                                  const char *enableCrossNodeConnection)
{
    datasystem::TraceGuard traceGuard = datasystem::Trace::Instance().SetRequestTraceUUID();
    return CreateObjectClient(cWorkerHost, workerPort, timeOut, token, tokenLen, clientPublicKey, cClientPublicKeyLen,
                              clientPrivateKey, clientPrivateKeyLen, serverPublicKey, cServerPublicKeyLen, accessKey,
                              cAccessKeyLen, secretKey, secretKeyLen, tenantId, cTenantIdLen,
                              enableCrossNodeConnection);
}
```

- [ ] **Step 4: Verify it compiles**

Run the c_api compile (CMake path used by the repo `build.sh`, or the equivalent Bazel build of the c_api target). Minimal check:
```bash
grep -c 'CreateObjectClientWithTrace' src/datasystem/c_api/util.h src/datasystem/c_api/util.cpp
```
Expected: `util.h` reports `1`, `util.cpp` reports `1`. Full build is exercised in Task 4.

- [ ] **Step 5: Commit**

```bash
git add src/datasystem/c_api/util.h src/datasystem/c_api/util.cpp
git commit -m "refactor(c_api): add shared CreateObjectClientWithTrace helper"
```

---

### Task 2: Delegate `KVCreateClient` to the shared helper

**Files:**
- Modify: `src/datasystem/c_api/kv_client_c_wrapper.cpp:40-52` (function body only)

**Interfaces:**
- Consumes: `CreateObjectClientWithTrace(...)` from Task 1 (already visible via the existing `#include "datasystem/c_api/util.h"` at the top of this file).
- Produces: unchanged public symbol `KVClient_p KVCreateClient(...)`.

- [ ] **Step 1: Replace the body**

Replace the two-statement body of `KVCreateClient` (the `TraceGuard` line plus the `return CreateObjectClient(...)` call) with a single delegation. The signature line stays exactly as-is; only the body changes:

```cpp
KVClient_p KVCreateClient(const char *cWorkerHost, const int workerPort, const int timeOut, const char *token,
                          size_t tokenLen, const char *clientPublicKey, size_t cClientPublicKeyLen,
                          const char *clientPrivateKey, size_t clientPrivateKeyLen, const char *serverPublicKey,
                          size_t cServerPublicKeyLen, const char *accessKey, size_t cAccessKeyLen,
                          const char *secretKey, size_t secretKeyLen, const char *tenantId, size_t cTenantIdLen,
                          const char *enableCrossNodeConnection)
{
    return CreateObjectClientWithTrace(cWorkerHost, workerPort, timeOut, token, tokenLen, clientPublicKey,
                                       cClientPublicKeyLen, clientPrivateKey, clientPrivateKeyLen, serverPublicKey,
                                       cServerPublicKeyLen, accessKey, cAccessKeyLen, secretKey, secretKeyLen, tenantId,
                                       cTenantIdLen, enableCrossNodeConnection);
}
```

- [ ] **Step 2: Verify no leftover local TraceGuard in this function**

Run:
```bash
sed -n '40,50p' src/datasystem/c_api/kv_client_c_wrapper.cpp
```
Expected: the body is the single `return CreateObjectClientWithTrace(...);` call — no `TraceGuard` line remains inside `KVCreateClient`.

- [ ] **Step 3: Commit**

```bash
git add src/datasystem/c_api/kv_client_c_wrapper.cpp
git commit -m "refactor(c_api): delegate KVCreateClient to shared helper"
```

---

### Task 3: Delegate `OCCreateClient` to the shared helper

**Files:**
- Modify: `src/datasystem/c_api/object_client_c_wrapper.cpp:37-49` (function body only)

**Interfaces:**
- Consumes: `CreateObjectClientWithTrace(...)` from Task 1 (visible via the existing `#include "datasystem/c_api/util.h"` at the top of this file).
- Produces: unchanged public symbol `ObjectClient_p OCCreateClient(...)`.

- [ ] **Step 1: Replace the body**

Replace the two-statement body of `OCCreateClient` with a single delegation. The signature line stays exactly as-is; only the body changes:

```cpp
ObjectClient_p OCCreateClient(const char *cWorkerHost, const int workerPort, const int timeOut, const char *token,
                              size_t tokenLen, const char *clientPublicKey, size_t cClientPublicKeyLen,
                              const char *clientPrivateKey, size_t clientPrivateKeyLen, const char *serverPublicKey,
                              size_t cServerPublicKeyLen, const char *accessKey, size_t cAccessKeyLen,
                              const char *secretKey, size_t secretKeyLen, const char *tenantId, size_t cTenantIdLen,
                              const char *enableCrossNodeConnection)
{
    return CreateObjectClientWithTrace(cWorkerHost, workerPort, timeOut, token, tokenLen, clientPublicKey,
                                       cClientPublicKeyLen, clientPrivateKey, clientPrivateKeyLen, serverPublicKey,
                                       cServerPublicKeyLen, accessKey, cAccessKeyLen, secretKey, secretKeyLen, tenantId,
                                       cTenantIdLen, enableCrossNodeConnection);
}
```

- [ ] **Step 2: Verify no leftover local TraceGuard in this function**

Run:
```bash
sed -n '37,47p' src/datasystem/c_api/object_client_c_wrapper.cpp
```
Expected: the body is the single `return CreateObjectClientWithTrace(...);` call — no `TraceGuard` line remains inside `OCCreateClient`.

- [ ] **Step 3: Commit**

```bash
git add src/datasystem/c_api/object_client_c_wrapper.cpp
git commit -m "refactor(c_api): delegate OCCreateClient to shared helper"
```

---

### Task 4: Verify build, ABI symbols, and existing tests

**Files:**
- No source changes; verification only.

**Interfaces:**
- Consumes: the three prior tasks.

- [ ] **Step 1: Confirm the duplication is gone (exactly one copy of the logic)**

Run:
```bash
grep -rn 'SetRequestTraceUUID' src/datasystem/c_api/util.cpp src/datasystem/c_api/kv_client_c_wrapper.cpp src/datasystem/c_api/object_client_c_wrapper.cpp | grep -i 'createclient\|CreateObjectClientWithTrace' || true
grep -c 'return CreateObjectClientWithTrace' src/datasystem/c_api/kv_client_c_wrapper.cpp src/datasystem/c_api/object_client_c_wrapper.cpp
```
Expected: each wrapper file reports `1`; the shared `SetRequestTraceUUID()` for client creation now appears only inside `CreateObjectClientWithTrace` in `util.cpp`.

- [ ] **Step 2: Build both c_api library targets**

Build the project's c_api libraries (per repo `build.sh` / CMake), ensuring both `datasystem_c` (SHARED) and `datasystem_c_static` (STATIC) targets compile and link.
Expected: clean build, no unresolved-symbol or signature-mismatch errors.

- [ ] **Step 3: Confirm both exported symbols still exist**

After the shared library builds, verify the ABI symbols are present (adjust the artifact path to the build output):
```bash
nm -D <build_output>/libdatasystem_c.so | grep -E 'KVCreateClient|OCCreateClient'
```
Expected: both `KVCreateClient` and `OCCreateClient` appear as defined (`T`) symbols.

- [ ] **Step 4: Run the existing Go binding tests**

Run the Go binding tests that call `C.KVCreateClient` / `C.OCCreateClient`:
```bash
# from the go module root, per repo test instructions
go test ./kv/... ./object/...
```
Expected: `go/kv/kv_client_test.go` and `go/object/object_client_test.go` pass unchanged (subject to the same worker prerequisites they had before this change). Behaviour at the create-client entry points is identical.

- [ ] **Step 5: Final commit (if any formatting fixups were needed)**

```bash
git add -A
git commit -m "test(c_api): verify CreateClient dedup preserves symbols and behavior" || echo "nothing to commit"
```

---

## Self-Review

**1. Spec coverage:**
- Scope "add helper to util.{h,cpp}" → Task 1. ✓
- Scope "rewrite KVCreateClient body" → Task 2. ✓
- Scope "rewrite OCCreateClient body" → Task 3. ✓
- Acceptance #1 (symbols preserved) → Task 4 Steps 2–3. ✓
- Acceptance #2 (same observable behaviour incl. nullptr on invalid host) → helper delegates to unchanged `CreateObjectClient`; Task 4 Step 4. ✓
- Acceptance #3 (logic in exactly one place) → Task 4 Step 1. ✓
- Acceptance #4 (both targets compile, Go tests pass) → Task 4 Steps 2 & 4. ✓
- Acceptance #5 (no unrelated changes) → tasks touch only the three named files. ✓
- Ledger L4 (CreateObjectClient unchanged) → no task modifies it. ✓
- Ledger L5 (no new C++ harness) → Task 4 uses existing Go tests + build only. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N" — every code step shows the full code. ✓

**3. Type consistency:** `CreateObjectClientWithTrace` uses the identical 17-parameter `void *` signature in the declaration (Task 1 Step 2), definition (Task 1 Step 3), and both call sites (Tasks 2–3). Return type `void *` is compatible with `KVClient_p`/`ObjectClient_p` (both `typedef void *`). ✓

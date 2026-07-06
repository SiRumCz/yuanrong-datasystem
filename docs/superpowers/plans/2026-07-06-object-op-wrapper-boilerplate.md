# Object-Op Wrapper Boilerplate De-duplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three identical TraceGuard + AccessRecorder + Execute + Record wrapper bodies in `object_client_c_wrapper.cpp` with a single shared, type-safe helper, preserving behavior and the C ABI.

**Architecture:** Add one file-local variadic function template (`RecordedObjectOp`) in an anonymous namespace that performs the shared scaffold — acquire a `TraceGuard`, build an `AccessRecorder` for a given `AccessRecorderKey`, declare a `RequestParam`, invoke the passed `OCExecute*` worker with the forwarded args plus `&reqParam`, call `Record(rc.code, "0", reqParam, rc.errMsg)`, and return the `StatusC`. Rewrite the three `extern "C"` ops to one-line delegations. No header, ABI, worker, or peer-file changes.

**Tech Stack:** C++ (variadic templates, perfect forwarding), Bazel + CMake build, existing Go integration tests via cgo.

## Global Constraints

- Do not change the public C signatures or `extern "C"` linkage of `OCGIncreaseRef`, `OCDeccreaseRef`, `OCReleaseGRefs` (declared in `src/datasystem/c_api/object_client_c_wrapper.h`).
- Do not touch `OCPut`, `OCGet`, the `OCExecute*` workers, or the kv/stream wrappers.
- Behavior must be byte-for-byte equivalent: same key, same worker, same `Record(rc.code, "0", reqParam, rc.errMsg)`, same return.
- Match existing file style (4-space indent, `datasystem::` qualification, no reformat of untouched lines). Surgical changes only.
- The helper's `Record` data-size argument is the literal `"0"` (all three call sites use it today).

---

### Task 1: Introduce the shared helper and delegate all three ops to it

**Files:**
- Modify: `src/datasystem/c_api/object_client_c_wrapper.cpp` — add helper after includes (near line 36); rewrite `OCGIncreaseRef` (177–188), `OCDeccreaseRef` (225–236), `OCReleaseGRefs` (257–265).
- Test (existing, do not create): `go/object/object_client_test.go` — exercises `GIncreaseRef`, `GDecreaseRef`, `ReleaseGRefs` through the C API.

**Interfaces:**
- Consumes: existing free functions `OCExecuteGIncreaseRef`, `OCExecuteGDecreaseRef`, `OCExecuteReleaseGRefs` (each ends with a `datasystem::RequestParam *reqParam` parameter); `datasystem::TraceGuard`, `datasystem::AccessRecorder`, `datasystem::RequestParam`, `datasystem::AccessRecorderKey`, and the `StatusC` struct (`code`, `errMsg`).
- Produces: file-local `template <typename ExecFn, typename... Args> StatusC RecordedObjectOp(datasystem::AccessRecorderKey key, ExecFn exec, Args &&... args)` in an anonymous namespace. Not exported; internal to this translation unit only.

- [ ] **Step 1: Confirm existing behavior compiles/tests green (baseline)**

Run (from repo root; use the project's standard C-API + Go test entry point — check `build.sh`/BUILD.bazel if unsure):
```bash
bazel test //go/object:object_client_test 2>&1 | tail -20 || echo "record baseline: note current pass/fail"
```
Expected: the object Go test target passes (or record the current state as the baseline to preserve). This establishes the behavior the refactor must keep.

- [ ] **Step 2: Add the shared helper**

In `src/datasystem/c_api/object_client_c_wrapper.cpp`, immediately after the include block (after line 35, before `OCCreateClient` at line 37), add:

```cpp
namespace {
// Shared scaffold for object-client public ops whose access metric is the constant "0":
// set up request tracing and access recording, run the worker (which fills reqParam),
// record the result, and return it. Keeps the three ops below identical in behavior.
template <typename ExecFn, typename... Args>
StatusC RecordedObjectOp(datasystem::AccessRecorderKey key, ExecFn exec, Args &&... args)
{
    datasystem::TraceGuard traceGuard = datasystem::Trace::Instance().SetRequestTraceUUID();
    datasystem::AccessRecorder accessPoint(key);
    datasystem::RequestParam reqParam;
    StatusC rc = exec(std::forward<Args>(args)..., &reqParam);
    accessPoint.Record(rc.code, "0", reqParam, rc.errMsg);
    return rc;
}
}  // namespace
```

- [ ] **Step 3: Rewrite `OCGIncreaseRef` to delegate**

Replace the body of `OCGIncreaseRef` (177–188) so the function becomes:

```cpp
struct StatusC OCGIncreaseRef(ObjectClient_p clientPtr, const char **cObjKeys, const size_t *cObjKeysLen,
                              uint64_t cObjKeysNum, char *cRemoteClientId, size_t cRemoteClientIdLen,
                              char **cFailedObjKeys, size_t *failedObjKeysCount)
{
    return RecordedObjectOp(datasystem::AccessRecorderKey::DS_OBJECT_CLIENT_GINCREASEREF,
                            OCExecuteGIncreaseRef, clientPtr, cObjKeys, cObjKeysLen, cObjKeysNum, cRemoteClientId,
                            cRemoteClientIdLen, cFailedObjKeys, failedObjKeysCount);
}
```

- [ ] **Step 4: Rewrite `OCDeccreaseRef` to delegate**

Replace the body of `OCDeccreaseRef` (225–236) so the function becomes:

```cpp
struct StatusC OCDeccreaseRef(ObjectClient_p clientPtr, const char **cObjKeys, const size_t *cObjKeysLen,
                              uint64_t cObjKeysNum, char *cRemoteClientId, size_t cRemoteClientIdLen,
                              char **cFailedObjKeys, size_t *failedObjKeysCount)
{
    return RecordedObjectOp(datasystem::AccessRecorderKey::DS_OBJECT_CLIENT_GDECREASEREF,
                            OCExecuteGDecreaseRef, clientPtr, cObjKeys, cObjKeysLen, cObjKeysNum, cRemoteClientId,
                            cRemoteClientIdLen, cFailedObjKeys, failedObjKeysCount);
}
```

- [ ] **Step 5: Rewrite `OCReleaseGRefs` to delegate**

Replace the body of `OCReleaseGRefs` (257–265) so the function becomes:

```cpp
struct StatusC OCReleaseGRefs(ObjectClient_p clientPtr, char *cRemoteClientId, size_t cRemoteClientIdLen)
{
    return RecordedObjectOp(datasystem::AccessRecorderKey::DS_OBJECT_CLIENT_RELEASEGREFS,
                            OCExecuteReleaseGRefs, clientPtr, cRemoteClientId, cRemoteClientIdLen);
}
```

- [ ] **Step 6: Confirm `<utility>` is available for `std::forward`**

`std::forward` requires `<utility>`. `object_client_c_wrapper.cpp` currently includes `<functional>` (line 24) which transitively provides it on the toolchains in use, but do not rely on transitive includes. Add `#include <utility>` to the `<c...>`/std include group (near line 24) if it is not already present. Verify:
```bash
grep -nE '#include <(utility|functional)>' src/datasystem/c_api/object_client_c_wrapper.cpp
```
Expected: `<utility>` appears. Add it if missing.

- [ ] **Step 7: Build the C API target**

Run (use the project's standard build for the c_api library — confirm the exact target from `src/datasystem/c_api/BUILD.bazel`):
```bash
bazel build //src/datasystem/c_api:... 2>&1 | tail -20
```
Expected: compiles with no errors. Template instantiation resolves each `exec` to the correct `OCExecute*` function pointer; `Record(rc.code, ...)` binds to the existing `int` overload (`rc.code` is `uint32_t`), matching pre-refactor behavior.

- [ ] **Step 8: Run the existing Go integration tests**

Run:
```bash
bazel test //go/object:object_client_test 2>&1 | tail -30
```
Expected: PASS — matches the Step 1 baseline. `GIncreaseRef`, `GDecreaseRef`, and `ReleaseGRefs` behave identically through the refactored wrappers.

- [ ] **Step 9: Diff review — confirm surgical scope**

Run:
```bash
git diff --stat && git diff src/datasystem/c_api/object_client_c_wrapper.cpp
```
Expected: only `object_client_c_wrapper.cpp` changed; additions are the helper plus the three one-line delegations; the three old bodies removed; no reformatting of untouched code; the header, workers, `OCPut`/`OCGet`, and kv/stream files untouched.

- [ ] **Step 10: Commit**

```bash
git add src/datasystem/c_api/object_client_c_wrapper.cpp
git commit -m "refactor(c_api): extract shared object-op wrapper scaffold into RecordedObjectOp"
```

---

## Self-Review

**1. Spec coverage:**
- Summary/Scope (single shared definition for the three ops) → Task 1 Steps 2–5.
- Behavior/acceptance (unchanged ABI, identical behavior, one place, builds, Go tests green) → Steps 1, 7, 8, 9.
- Out-of-scope guarantees (Put/Get, kv/stream, workers, header untouched) → Step 9 diff check + Global Constraints.
- Ledger L1 (template not macro) → helper in Step 2 is a template. L2 (`"0"` hard-coded) → Step 2. L3 (file-local anonymous namespace) → Step 2. L4 (extern "C"/ABI unchanged) → Steps 3–5 keep signatures, Step 9 verifies. L5 (trailing `RequestParam *`) → Step 2 appends `&reqParam`. L6 (rely on existing Go tests) → Steps 1 & 8.

**2. Placeholder scan:** No TBD/TODO; every code step shows full code; build/test commands are explicit (with a note to confirm exact Bazel target labels from the local BUILD files, since label spelling can vary).

**3. Type consistency:** `RecordedObjectOp` signature is identical everywhere it is referenced; each call passes `AccessRecorderKey` then the matching `OCExecute*` then the op's forwarded args; `StatusC.code`/`.errMsg` usage matches the struct definition in `status_definition.h`.

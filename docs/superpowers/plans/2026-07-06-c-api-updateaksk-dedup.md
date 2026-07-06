# Deduplicate UpdateAkSk C-API Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the three byte-identical `*UpdateAkSk` C-API wrapper bodies into one shared function template, removing the redundant `accessKey.assign` no-op, with zero change to observable behavior.

**Architecture:** Add a header-only function template `UpdateAkSkImpl<ClientT>` to the existing shared helper header `src/datasystem/c_api/util.h` (which already hosts `ConnectWorker`/`FreeClient` and is `#include`d by all three wrapper `.cpp` files). Each of `SCUpdateAkSk`, `OCUpdateAkSk`, `StreamUpdateAkSk` becomes a single delegating `return`, instantiating the template with its concrete client type.

**Tech Stack:** C++ (C-ABI wrappers), Bazel + CMake build, GoogleTest ST suite.

## Global Constraints

- Preserve all three exported C symbols and signatures exactly; no `*_c_wrapper.h` changes — verbatim from spec Scope.
- No `BUILD.bazel` / `CMakeLists.txt` edits — the helper is header-only in an already-included header.
- KV wrapper instantiates with `datasystem::object_cache::ObjectClientImpl`; Object wrapper with `datasystem::object_cache::ObjectClientImpl`; Stream wrapper with `datasystem::StreamClient` (spec criterion 6, ledger L3).
- Return semantics unchanged: error → `ToStatusC(rc)`, success → `StatusC{ datasystem::K_OK, {} }`.
- Drop the redundant `accessKey.assign(cAccessKey, cAccessKeyLen)` (ledger L2); construct `accessKey` from `(cAccessKey, cAccessKeyLen)` and `secretKey` as `datasystem::SensitiveValue(cSecretKey, cSecretKeyLen)`.
- Surgical change only — do not touch adjacent wrapper functions or formatting.

---

### Task 1: Add the shared `UpdateAkSkImpl` template to `util.h`

**Files:**
- Modify: `src/datasystem/c_api/util.h` (add template near the `ConnectWorker`/`FreeClient` declarations, before the closing `#endif`)

**Interfaces:**
- Consumes: `ToStatusC(datasystem::Status &)`, `struct StatusC`, `datasystem::SensitiveValue`, `datasystem::Status`, `datasystem::K_OK` — all already visible in `util.h` via its existing includes (`object_client_impl.h`, `sensitive_value.h`, `status.h`, `status_definition.h`).
- Produces: `template <typename ClientT> StatusC UpdateAkSkImpl(void *clientPtr, const char *cAccessKey, size_t cAccessKeyLen, const char *cSecretKey, size_t cSecretKeyLen)` — instantiated by Tasks 2–4.

- [ ] **Step 1: Add the template definition**

Insert immediately after the `FreeClient(void *clientPtr);` declaration in `src/datasystem/c_api/util.h` (before `GetObjKeysVector`):

```cpp
/**
 * @brief Shared implementation for the kv/object/stream UpdateAkSk C wrappers. Casts the opaque
 *        handle to the concrete client type, updates the AK/SK, and maps the result to StatusC.
 * @tparam ClientT The concrete client implementation type the handle wraps.
 * @param[in] clientPtr The opaque client handle (a std::shared_ptr<ClientT> *).
 * @param[in] cAccessKey The access key bytes.
 * @param[in] cAccessKeyLen The access key length.
 * @param[in] cSecretKey The secret key bytes.
 * @param[in] cSecretKeyLen The secret key length.
 * @return C style status: K_OK on success, otherwise the mapped error.
 */
template <typename ClientT>
StatusC UpdateAkSkImpl(void *clientPtr, const char *cAccessKey, size_t cAccessKeyLen, const char *cSecretKey,
                       size_t cSecretKeyLen)
{
    auto client = reinterpret_cast<std::shared_ptr<ClientT> *>(clientPtr);
    std::string accessKey(cAccessKey, cAccessKeyLen);
    datasystem::SensitiveValue secretKey(cSecretKey, cSecretKeyLen);
    datasystem::Status rc = (*client)->UpdateAkSk(accessKey, secretKey);
    if (rc.IsError()) {
        return ToStatusC(rc);
    }
    return StatusC{ datasystem::K_OK, {} };
}
```

- [ ] **Step 2: Verify the header still parses (syntax-only)**

Run: `g++ -std=c++17 -fsyntax-only -Isrc -Iinclude src/datasystem/c_api/util.h 2>&1 | head -40`
Expected: no errors originating from the added template. (Include-path noise for unrelated transitive headers is acceptable; if a `std::shared_ptr` / `std::string` "not declared" error points at the new template, add `#include <memory>` and `#include <string>` to `util.h` — see ledger L4 — and re-run.)

- [ ] **Step 3: Commit**

```bash
git add src/datasystem/c_api/util.h
git commit -m "refactor(c_api): add shared UpdateAkSkImpl template helper"
```

---

### Task 2: Delegate `SCUpdateAkSk` (kv wrapper) to the helper

**Files:**
- Modify: `src/datasystem/c_api/kv_client_c_wrapper.cpp` (`SCUpdateAkSk`, currently lines 60–72)

**Interfaces:**
- Consumes: `UpdateAkSkImpl<datasystem::object_cache::ObjectClientImpl>` from Task 1.

- [ ] **Step 1: Replace the body**

Replace the entire `SCUpdateAkSk` function body with a single delegating return (keep the signature verbatim):

```cpp
struct StatusC SCUpdateAkSk(KVClient_p clientPtr, const char *cAccessKey, size_t cAccessKeyLen, const char *cSecretKey,
                            size_t cSecretKeyLen)
{
    return UpdateAkSkImpl<datasystem::object_cache::ObjectClientImpl>(clientPtr, cAccessKey, cAccessKeyLen, cSecretKey,
                                                                      cSecretKeyLen);
}
```

- [ ] **Step 2: Confirm no orphaned includes**

The file still uses `object_client_impl.h` and `util.h` in other functions, so no include is removed. Verify: `grep -c "ObjectClientImpl" src/datasystem/c_api/kv_client_c_wrapper.cpp` returns a value > 1 (other reinterpret_casts remain).
Expected: a count greater than 1.

- [ ] **Step 3: Commit**

```bash
git add src/datasystem/c_api/kv_client_c_wrapper.cpp
git commit -m "refactor(c_api): route SCUpdateAkSk through shared helper"
```

---

### Task 3: Delegate `OCUpdateAkSk` (object wrapper) to the helper

**Files:**
- Modify: `src/datasystem/c_api/object_client_c_wrapper.cpp` (`OCUpdateAkSk`, currently lines 57–69)

**Interfaces:**
- Consumes: `UpdateAkSkImpl<datasystem::object_cache::ObjectClientImpl>` from Task 1.

- [ ] **Step 1: Replace the body**

```cpp
struct StatusC OCUpdateAkSk(ObjectClient_p clientPtr, const char *cAccessKey, size_t cAccessKeyLen,
                            const char *cSecretKey, size_t cSecretKeyLen)
{
    return UpdateAkSkImpl<datasystem::object_cache::ObjectClientImpl>(clientPtr, cAccessKey, cAccessKeyLen, cSecretKey,
                                                                      cSecretKeyLen);
}
```

- [ ] **Step 2: Confirm includes intact**

Run: `grep -n "util.h\|object_client_impl.h" src/datasystem/c_api/object_client_c_wrapper.cpp`
Expected: both includes still present (used by other functions).

- [ ] **Step 3: Commit**

```bash
git add src/datasystem/c_api/object_client_c_wrapper.cpp
git commit -m "refactor(c_api): route OCUpdateAkSk through shared helper"
```

---

### Task 4: Delegate `StreamUpdateAkSk` (stream wrapper) to the helper

**Files:**
- Modify: `src/datasystem/c_api/stream_client_c_wrapper.cpp` (`StreamUpdateAkSk`, currently lines 74–86)

**Interfaces:**
- Consumes: `UpdateAkSkImpl<datasystem::StreamClient>` from Task 1.

- [ ] **Step 1: Replace the body**

```cpp
struct StatusC StreamUpdateAkSk(StreamClient_p clientPtr, const char *cAccessKey, size_t cAccessKeyLen,
                                const char *cSecretKey, size_t cSecretKeyLen)
{
    return UpdateAkSkImpl<datasystem::StreamClient>(clientPtr, cAccessKey, cAccessKeyLen, cSecretKey, cSecretKeyLen);
}
```

- [ ] **Step 2: Confirm `stream_client.h` is included**

Run: `grep -n "stream_client.h\|util.h" src/datasystem/c_api/stream_client_c_wrapper.cpp`
Expected: both `datasystem/stream_client.h` and `datasystem/c_api/util.h` present, so `datasystem::StreamClient` is a complete type at the instantiation site.

- [ ] **Step 3: Commit**

```bash
git add src/datasystem/c_api/stream_client_c_wrapper.cpp
git commit -m "refactor(c_api): route StreamUpdateAkSk through shared helper"
```

---

### Task 5: Build and regression-verify

**Files:**
- None (verification only)

- [ ] **Step 1: Build the affected targets**

Run the project's standard build for the c_api library (per repo tooling), e.g.:
`bash build.sh` or the Bazel target that compiles `//src/datasystem/c_api:...`.
Expected: all three wrapper TUs compile cleanly with no new warnings. If an incomplete-type or unknown-symbol error appears in exactly the new template (ledger L4), add the minimal missing `#include` to `util.h` and rebuild.

- [ ] **Step 2: Run the AK/SK auth ST tests where the environment allows**

Run the existing AK/SK auth suites, e.g.:
`tests/st/client/kv_cache/kv_client_tenant_akskauth_test` and
`tests/st/client/stream_cache/sc_client_aksk_auth_test`.
Expected: pass (unchanged from before). If the ST environment (live worker) is unavailable in CI, record that these were not run and rely on the clean build as the primary gate (ledger L6).

- [ ] **Step 3: Confirm no unintended diff**

Run: `git diff --stat main -- src/datasystem/c_api/`
Expected: exactly four files changed (`util.h` + three wrappers); each wrapper diff is a net reduction (removed body, added one-line delegate).

- [ ] **Step 4: Final commit (if any fixup from Step 1)**

```bash
git add -A src/datasystem/c_api/
git commit -m "refactor(c_api): finalize UpdateAkSk dedup"
```

---

## Self-Review

- **Spec coverage:** Summary/Scope → Tasks 1–4; acceptance criteria 1–6 → Tasks 2–4 (signatures kept, single-return bodies, correct template args, `.assign` dropped); criterion 7 (build + ST tests) → Task 5 Steps 1–2; criterion 8 (no BUILD edits) → Global Constraints + Task 5 Step 3. All covered.
- **Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output.
- **Type consistency:** `UpdateAkSkImpl<ClientT>(void*, const char*, size_t, const char*, size_t)` defined in Task 1 is used with identical signature and argument order in Tasks 2–4; `ObjectClientImpl` for kv/object, `StreamClient` for stream — matches spec criterion 6 and ledger L3.

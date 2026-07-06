# Spec: Deduplicate the UpdateAkSk C-API client wrapper (kv/object/stream)

Issue: SiRumCz/yuanrong-datasystem#155 — `[duplicate-code] Identical UpdateAkSk client wrapper duplicated across kv/object/stream C-API wrappers`

## Summary

`SCUpdateAkSk`, `OCUpdateAkSk`, and `StreamUpdateAkSk` have byte-identical bodies. Each
reinterpret-casts the opaque client handle to a `std::shared_ptr<ClientImpl> *`, builds a
`std::string accessKey`, redundantly re-`assign`s the same bytes into it, constructs a
`datasystem::SensitiveValue secretKey`, calls `(*client)->UpdateAkSk(accessKey, secretKey)`,
and maps the result to `StatusC` (error → `ToStatusC(rc)`, success → `StatusC{ K_OK, {} }`).
The only differences are the exported function name and the concrete client implementation type:

- `src/datasystem/c_api/kv_client_c_wrapper.cpp` `SCUpdateAkSk` → casts to `datasystem::object_cache::ObjectClientImpl` (verified: the entire KV wrapper backs its handle with `ObjectClientImpl`).
- `src/datasystem/c_api/object_client_c_wrapper.cpp` `OCUpdateAkSk` → casts to `datasystem::object_cache::ObjectClientImpl`.
- `src/datasystem/c_api/stream_client_c_wrapper.cpp` `StreamUpdateAkSk` → casts to `datasystem::StreamClient`.

This spec extracts the shared body into one function template in the existing shared header
`src/datasystem/c_api/util.h` (which already hosts the sibling shared helpers `ConnectWorker`
and `FreeClient` and is included by all three `.cpp` files), parameterized on the client type.
The redundant `accessKey.assign(...)` line is dropped (it collapses to a single site, which is the
issue's stated intent). Observable behavior of all three exported C functions is preserved exactly.

## Scope

In scope:
- Add one function-template helper to `src/datasystem/c_api/util.h`.
- Rewrite the three `*UpdateAkSk` wrapper bodies to a single delegating `return` each.
- Keep the three exported C symbols, their signatures, and their return semantics unchanged.

Out of scope / explicitly NOT touched:
- The public C API headers (`*_c_wrapper.h`) — signatures are unchanged.
- The `TraceGuard`/tracing behavior — note none of the three current `*UpdateAkSk` bodies install a `TraceGuard` (unlike `SCConnectWorker`), so none is added; parity is preserved.
- Any other duplicated wrapper functions (e.g. `ConnectWorker`, `FreeClient`) — not part of this issue.
- The underlying `ObjectClientImpl::UpdateAkSk` / `StreamClient::UpdateAkSk` implementations.
- The KV-handle-backed-by-`ObjectClientImpl` design fact (pre-existing, consistent across the whole KV wrapper) — preserved, not "fixed".

## Behavior / acceptance criteria

1. The three exported symbols `SCUpdateAkSk`, `OCUpdateAkSk`, `StreamUpdateAkSk` keep identical
   signatures, linkage, and observable return values.
2. For each: on `(*client)->UpdateAkSk(...)` returning an error, the wrapper returns `ToStatusC(rc)`;
   on success it returns `StatusC{ datasystem::K_OK, {} }`. Byte-for-byte the prior behavior.
3. `accessKey` is constructed from `(cAccessKey, cAccessKeyLen)`; `secretKey` is
   `datasystem::SensitiveValue(cSecretKey, cSecretKeyLen)`. Same argument order/lengths as before.
4. The redundant `accessKey.assign(cAccessKey, cAccessKeyLen)` no-op is removed (single call site now).
5. Each of the three wrapper functions has a body reduced to one `return <helper><ClientT>(...);`.
6. The KV wrapper's helper is instantiated with `datasystem::object_cache::ObjectClientImpl`, the
   object wrapper's with `datasystem::object_cache::ObjectClientImpl`, and the stream wrapper's with
   `datasystem::StreamClient`.
7. The project compiles (all three TUs) with no new warnings from the change; existing AK/SK
   auth ST tests (`tests/st/client/.../kv_client_tenant_akskauth_test.cpp`,
   `tests/st/client/stream_cache/sc_client_aksk_auth_test.cpp`) continue to pass where the
   environment permits running them.
8. No change to any `BUILD.bazel` / `CMakeLists.txt` (the helper is header-only in an
   already-included header, so no new translation unit or dependency edge is introduced).

## Accountability Ledger

- **L1 — DECISION** — *what:* Place the shared logic as a `template<typename ClientT>` free
  function in `src/datasystem/c_api/util.h` (header-only), rather than a non-template `void*`
  helper in `util.cpp`. *why:* The three sites differ only by client type; a member call
  `(*client)->UpdateAkSk(...)` needs the concrete type, which a `void*` non-template cannot supply
  without a second dispatch. A header-only template matches the issue's "templated/shared helper"
  request and needs no BUILD edits. *what-I-did:* Chose the template-in-util.h approach.
  *confidence:* high. *blast radius:* low + WHY: only the three wrapper `.cpp` files instantiate it;
  no other caller and no ABI change. *reversibility:* reversible + WHY: it is an internal helper;
  inlining it back is mechanical. *revisit-if:* a reviewer prefers explicit-instantiation in
  `util.cpp`, or a non-template `void*` dispatch is mandated for header-size reasons.

- **L2 — DECISION** — *what:* Drop the redundant `accessKey.assign(cAccessKey, cAccessKeyLen)` line
  entirely (do not preserve it in the helper). *why:* The line reassigns the exact same bytes into a
  `std::string` just constructed from them — a pure no-op; the issue explicitly flags it as the bug
  to fix and notes the fix must apply to all three sites, which extraction achieves. *what-I-did:*
  Excluded the `.assign` from the extracted helper. *confidence:* high. *blast radius:* low + WHY:
  removing a self-assignment cannot change `accessKey`'s value or length. *reversibility:* reversible
  + WHY: re-adding one line if ever wanted. *revisit-if:* review insists on a literal
  no-behavior-change extraction that keeps the dead line.

- **L3 — ASSUMPTION** (verified) — *what:* The KV wrapper's `SCUpdateAkSk` casting the handle to
  `datasystem::object_cache::ObjectClientImpl` is intentional and correct, not a latent bug to
  "correct" to a KV type. *why:* If it were wrong, the helper's template argument for KV would be
  ambiguous. *what-I-did:* Verified — every `reinterpret_cast` in `kv_client_c_wrapper.cpp`
  (lines 63, 91, 137, 186, 226, 278, 313, …) casts to `std::shared_ptr<ObjectClientImpl> *`; the KV
  handle is uniformly `ObjectClientImpl`-backed. Instantiate the KV helper with `ObjectClientImpl`.
  *confidence:* high. *blast radius:* low + WHY: preserving the existing type keeps behavior
  identical. *reversibility:* reversible. *revisit-if:* a separate issue re-types the KV handle.

- **L4 — ASSUMPTION** (verified) — *what:* `util.h` already transitively provides everything the
  template body needs (`std::shared_ptr`, `std::string`, `datasystem::SensitiveValue`,
  `datasystem::Status`, `datasystem::K_OK`, `StatusC`, `ToStatusC`) so no new `#include` is required
  in `util.h`, and the concrete client types are complete at each instantiation site (each `.cpp`
  already includes its impl header). *why:* A template body is only type-checked at instantiation;
  the header must still name the utility types it references directly. *what-I-did:* Verified —
  `util.h` includes `object_client_impl.h`, `sensitive_value.h`, `status.h`, `status_definition.h`,
  and declares `ToStatusC`; it already uses `std::string`/`std::shared_ptr` in existing declarations.
  The three `.cpp` files each include their client impl / `stream_client.h` header. *confidence:*
  medium. *blast radius:* low + WHY: at worst a missing-include compile error surfaces immediately at
  build. *reversibility:* reversible + WHY: add the missing `#include` if the build complains.
  *revisit-if:* the build reports an incomplete-type or unknown-symbol error in any of the three TUs.

- **L5 — DECISION** — *what:* Name the helper `UpdateAkSkImpl` (template) to disambiguate it from the
  member function `UpdateAkSk` it calls, rather than reusing the bare name `UpdateAkSk` in the style
  of `ConnectWorker`/`FreeClient`. *why:* Readability — `(*client)->UpdateAkSk(...)` inside a
  same-named free function is confusing; sibling helpers happen to have non-colliding names.
  *what-I-did:* Chose `UpdateAkSkImpl`. *confidence:* medium. *blast radius:* low + WHY: internal
  name only. *reversibility:* reversible + WHY: rename is mechanical. *revisit-if:* reviewer prefers
  matching the plain `ConnectWorker`/`FreeClient` naming convention.

- **L6 — DEFERRED** — *what:* No new isolated unit test is added for the three wrappers. *why:* The
  C wrappers require a live client handle / worker; existing coverage is ST-level AK/SK auth tests,
  and this repo has no unit harness that constructs these handles in isolation. Adding one is larger
  than this dedup and risks scope creep against the "surgical change" rule. *what-I-did:* Rely on
  existing AK/SK ST tests plus compile verification; documented the gap. *confidence:* medium.
  *blast radius:* medium + WHY: a pure behavior-preserving extraction with no new logic keeps
  regression risk low, but absence of a targeted test means a subtle instantiation error would only
  be caught by the broader ST suite. *reversibility:* reversible + WHY: a test can be added later.
  *revisit-if:* the reviewer requires a dedicated regression test, or a unit harness for the C
  wrappers already exists and I missed it.

## READ THESE FIRST

Risk-sorted (lowest-confidence × highest blast/irreversibility first):

1. **L6** (DEFERRED, medium confidence, medium blast) — no dedicated test; leans on ST coverage + compile.
2. **L4** (ASSUMPTION, medium confidence) — util.h transitively supplies the template's dependencies.
3. **L5** (DECISION, medium confidence) — helper naming (`UpdateAkSkImpl`).
4. **L1** (DECISION, high confidence) — template-in-util.h location.
5. **L2** (DECISION, high confidence) — drop the redundant `accessKey.assign`.
6. **L3** (ASSUMPTION verified, high confidence) — KV handle is `ObjectClientImpl`-backed.

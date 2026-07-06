# Design spec — Deduplicate the CreateClient trace-and-delegate wrapper (kv & object C-API)

Issue: #156 — `[duplicate-code] Identical CreateClient trace-and-delegate wrapper (kv and object C-API)`
Severity: high · Date: 2026-07-06

## Summary

`KVCreateClient` (`src/datasystem/c_api/kv_client_c_wrapper.cpp:40`) and `OCCreateClient`
(`src/datasystem/c_api/object_client_c_wrapper.cpp:37`) are byte-for-byte identical bodies:
each establishes a request-scoped `datasystem::TraceGuard` via
`datasystem::Trace::Instance().SetRequestTraceUUID()` and then forwards all 17 parameters
unchanged to the shared factory `CreateObjectClient` (declared in
`src/datasystem/c_api/util.h:156`, defined in `src/datasystem/c_api/util.cpp:297`). They
differ only in name and in a cosmetic return-type alias — `KVClient_p` vs `ObjectClient_p`,
both of which are `typedef void *` and identical to `CreateObjectClient`'s `void *` return.

This design extracts the duplicated *trace-and-delegate* logic into a single shared helper,
`CreateObjectClientWithTrace`, added beside `CreateObjectClient` in the c_api util layer.
Both public `extern "C"` entry points are slimmed to a one-line delegation to that helper.
Behaviour at the ABI boundary is preserved exactly; both exported symbols are retained
because external language bindings (Go cgo, and by extension Python/Java) resolve them by
name.

## Scope

In scope:
- Add `CreateObjectClientWithTrace(...)` (17-param, returns `void *`) to
  `src/datasystem/c_api/util.h` and `src/datasystem/c_api/util.cpp`. Its body holds the
  single copy of the `TraceGuard` + `CreateObjectClient(...)` logic.
- Rewrite the body of `KVCreateClient` in `kv_client_c_wrapper.cpp` to
  `return CreateObjectClientWithTrace(...);`.
- Rewrite the body of `OCCreateClient` in `object_client_c_wrapper.cpp` to
  `return CreateObjectClientWithTrace(...);`.

Out of scope:
- No change to the public headers `kv_client_c_wrapper.h` / `object_client_c_wrapper.h`
  (the exported signatures and `extern "C"` symbols are unchanged).
- No change to `CreateObjectClient` itself — it stays a pure factory with no tracing
  side effect.
- No change to the Go/Python/Java bindings.
- No new C++ unit-test harness for the c_api layer (none exists today) — see Ledger L5.
- The many other `SC*`/`OC*` wrappers that repeat the `TraceGuard` line are unrelated to
  this issue and are left untouched (surgical change).

## Behavior / acceptance criteria

1. The exported `extern "C"` symbols `KVCreateClient` and `OCCreateClient` still exist with
   identical signatures and return types (`KVClient_p` / `ObjectClient_p`, both `void *`).
2. Calling either symbol produces the same observable behaviour as before: a request trace
   UUID is set for the duration of the call, an `ObjectClientImpl` handle is constructed and
   returned, and an invalid host/port still returns `nullptr` (the guard path inside
   `CreateObjectClient`).
3. The `TraceGuard` + `CreateObjectClient(...)` logic exists in exactly one place
   (`CreateObjectClientWithTrace`); neither wrapper contains a second copy.
4. The project compiles (both `datasystem_c` shared and `datasystem_c_static` targets) and
   the existing Go binding tests (`go/kv/kv_client_test.go`, `go/object/object_client_test.go`)
   that invoke `C.KVCreateClient` / `C.OCCreateClient` continue to pass.
5. No unrelated files, formatting, or wrappers are modified.

## Accountability Ledger

Gaps filled autonomously while designing. Each entry: category · what · why · what-I-did ·
confidence · blast radius · reversibility · revisit-if. IDs are cross-referenced by the
evidence JSON.

- **L2 — ASSUMPTION (verified):** Both `KVCreateClient` and `OCCreateClient` are `extern "C"`
  ABI symbols consumed by external language bindings, so **both must be preserved** — neither
  may be deleted or renamed. *Why it matters:* the dedup must not collapse the two public
  symbols into one. *What I did:* verified `extern "C"` in both headers
  (`kv_client_c_wrapper.h:26`, `object_client_c_wrapper.h:26`) and confirmed Go cgo binds
  `C.KVCreateClient` (`go/kv/kv_client.go:147`) and `C.OCCreateClient`
  (`go/object/object_client.go:152`); kept both entry points, deduplicating only their bodies.
  Confidence: high. Blast radius: **high** — removing/renaming an exported symbol breaks the
  cross-language ABI and every downstream binding build. Reversibility: **costly** — a shipped
  ABI break requires coordinated downstream fixes. Verified: yes. Revisit if: a maintainer
  confirms one symbol is truly unused and may be dropped.

- **L1 — DECISION:** Extraction strategy = introduce a dedicated shared helper
  `CreateObjectClientWithTrace` in `util.{h,cpp}`; both wrappers delegate to it. Chosen over
  (B) moving the `TraceGuard` line down into `CreateObjectClient`, and over (C) symbol
  aliasing (L6). *Why:* keeps a single, named, documented home for the trace-and-create logic
  while leaving the existing `CreateObjectClient` factory contract untouched. *What I did:*
  specified the new helper and the two one-line delegations. Confidence: high. Blast radius:
  **medium** — `util.h`/`util.cpp` are shared by the kv, object, and stream wrappers, so a new
  symbol there is broadly visible. Reversibility: **reversible** — pure internal refactor, no
  persisted state or exported-symbol change. Revisit if: review prefers option B for
  minimality.

- **L5 — DEVIATION:** Do **not** add a new C++ unit-test harness; rely on the existing Go
  binding tests plus compilation to satisfy "add/keep tests." *Why:* there is no C++
  unit-test harness for the c_api wrappers today, the change is pure delegation and
  behaviour-preserving, and both symbols are already exercised through
  `go/kv/kv_client_test.go` and `go/object/object_client_test.go`. Standing up a new c_api UT
  target would be disproportionate scope for this refactor. *What I did:* scoped testing to
  "existing Go tests still pass + both targets compile." Confidence: medium. Blast radius:
  **low** — test-only. Reversibility: **reversible**. Revisit if: a c_api C++ UT target is
  later added, or a maintainer requires a dedicated regression test.

- **L3 — ASSUMPTION (verified):** `CreateObjectClient` has no callers other than these two
  wrappers, and `KVClient_p`/`ObjectClient_p` are both `typedef void *`, identical to the
  factory's `void *` return — so a single `void *`-returning helper serves both without any
  cast churn. *Why it matters:* justifies one shared helper rather than two type-specialized
  ones. *What I did:* grepped the repo for `CreateObjectClient` (only the two wrappers + its
  own decl/def) and confirmed the typedefs at `kv_client_c_wrapper.h:33` and
  `object_client_c_wrapper.h:32`. Confidence: high. Blast radius: **low**. Reversibility:
  **reversible**. Verified: yes. Revisit if: a new direct caller of `CreateObjectClient`
  appears.

- **L4 — DECISION:** Keep `CreateObjectClient` unchanged — a pure factory with no tracing
  side effect; the `TraceGuard` lives only in the new helper. *Why:* `CreateObjectClient` is
  declared and documented in `util.h`; folding a trace side effect into it would silently
  broaden its contract for any future caller. *What I did:* placed the guard exclusively in
  `CreateObjectClientWithTrace`. Confidence: high. Blast radius: **low** — additive; existing
  contract preserved. Reversibility: **reversible**. Revisit if: tracing is later desired at
  the factory level for all callers.

- **L6 — DECISION:** Reject the `__attribute__((alias("...")))` symbol-aliasing approach that
  would collapse both public symbols onto one definition. *Why:* it is compiler-specific
  (GCC/Clang), fragile across the C ABI and cross-language boundary, and harder to read than a
  plain delegation. *What I did:* excluded it; both symbols remain real functions that
  delegate. Confidence: high. Blast radius: **low**. Reversibility: **reversible**. Revisit
  if: binary size or symbol-count constraints ever justify aliasing and the toolchain is
  pinned.

- **L7 — ASSUMPTION (verified):** `TraceGuard` is a stack-scoped RAII object; relocating the
  identical `SetRequestTraceUUID()` call into the shared helper preserves the same trace
  scope and lifetime (the guard lives across the `CreateObjectClient` call and is cleared when
  the helper returns), so tracing behaviour is unchanged. *Why it matters:* confirms the move
  is behaviour-preserving for observability, not just correctness. *What I did:* verified
  `TraceGuard`/`SetRequestTraceUUID` in `common/log/trace.h` (RAII guard cleared on
  destruction) and that the helper's return point matches the wrappers' original return point.
  Confidence: high. Blast radius: **low** — observability only. Reversibility: **reversible**.
  Verified: yes. Revisit if: `TraceGuard` semantics change.

- **L8 — DECISION:** Place the shared helper in `util.{h,cpp}` (beside `CreateObjectClient`)
  rather than in either wrapper header, so both wrappers reach it through their existing
  `#include "datasystem/c_api/util.h"` with no new dependency, and ownership stays in the
  c_api util layer. *Why:* both wrappers already include `util.h`; no new include edges are
  introduced. *What I did:* specified the declaration in `util.h` and definition in
  `util.cpp`. Confidence: high. Blast radius: **low**. Reversibility: **reversible**. Revisit
  if: the util layer is reorganized.

## READ THESE FIRST

Ledger ids, risk-descending (low-confidence × high-blast / irreversible first):

1. **L2** (risk 3) — both `extern "C"` symbols must be preserved; do not collapse the ABI.
2. **L1** (risk 1) — chosen extraction strategy (dedicated shared helper).
3. **L5** (risk 1) — no new C++ test harness; rely on existing Go tests + compile.
4. **L3** (risk 0) — single `void *` helper serves both aliased types.
5. **L4** (risk 0) — `CreateObjectClient` left as a pure factory.
6. **L6** (risk 0) — symbol-aliasing rejected.
7. **L7** (risk 0) — trace scope/lifetime preserved by the move.
8. **L8** (risk 0) — helper lives in `util.{h,cpp}`.

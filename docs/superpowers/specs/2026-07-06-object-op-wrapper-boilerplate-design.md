# Design: De-duplicate public-op wrapper boilerplate (TraceGuard + AccessRecorder + Execute + Record)

Issue: #157 — [duplicate-code] Repeated public-op wrapper boilerplate.

## Summary

Three `extern "C"` public ops in `src/datasystem/c_api/object_client_c_wrapper.cpp` —
`OCGIncreaseRef`, `OCDeccreaseRef`, `OCReleaseGRefs` — are structurally identical
wrappers. Each one:

1. constructs a `datasystem::TraceGuard` from `Trace::Instance().SetRequestTraceUUID()`,
2. constructs a `datasystem::AccessRecorder` for the op's `AccessRecorderKey`,
3. declares a `datasystem::RequestParam reqParam`,
4. calls the matching `OCExecute*` worker forwarding all its arguments plus `&reqParam`,
5. calls `accessPoint.Record(rc.code, "0", reqParam, rc.errMsg)`,
6. returns the `StatusC rc`.

They differ only in the `AccessRecorderKey` and in which `OCExecute*` worker they
delegate to (which in turn changes the forwarded argument list). This design extracts
that scaffold into one shared, type-safe variadic function template used by all three
call sites, preserving behavior exactly and keeping the public C ABI unchanged.

## Scope

In scope:

- `src/datasystem/c_api/object_client_c_wrapper.cpp`: add one file-local helper
  (a variadic function template in an anonymous namespace) and rewrite the bodies of
  `OCGIncreaseRef` (177–188), `OCDeccreaseRef` (225–236), `OCReleaseGRefs` (257–265)
  to call it.

Explicitly out of scope:

- `OCPut` (116) and `OCGet` (129): same scaffold shape but the `Record` data-size
  argument is a real value (`std::to_string(cValLen)` / `std::to_string(totalSize)`),
  not the constant `"0"`. Folding them in would require changing the metric contract;
  left untouched to keep behavior identical and the change surgical.
- The peer wrappers in `kv_client_c_wrapper.cpp` / `stream_client_c_wrapper.cpp`.
  Generalizing to a shared header helper across all three translation units is a
  larger refactor deferred to a follow-up.
- The `OCExecute*` worker functions, the public header, and the C ABI: unchanged.

## Behavior / acceptance criteria

- The public C signatures and `extern "C"` linkage of the three ops are unchanged
  (verified against `object_client_c_wrapper.h`): the ABI and all Go/C callers are
  unaffected.
- For each of the three ops, the observable behavior is byte-for-byte equivalent to
  today: same `TraceGuard` acquisition, same `AccessRecorderKey`, same `RequestParam`
  passed to the same `OCExecute*` worker, same `Record(rc.code, "0", reqParam, rc.errMsg)`
  call, same returned `StatusC`.
- The three duplicated 10–12 line bodies collapse to a single-line delegation each;
  the shared scaffold exists in exactly one place.
- The project builds (Bazel/CMake C API target compiles).
- Existing Go integration tests in `go/object/object_client_test.go` that exercise
  `GIncreaseRef`, `GDecreaseRef`, and `ReleaseGRefs` through the C API continue to
  pass. No new test is required to prove the refactor because these paths are already
  covered; keep them green.

### Proposed shared helper (illustrative)

```cpp
namespace {
// Shared scaffold for object-client public ops whose access metric is a constant "0":
// set up trace + access recording, run the worker (which fills reqParam), record, return.
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

Call site example:

```cpp
struct StatusC OCReleaseGRefs(ObjectClient_p clientPtr, char *cRemoteClientId, size_t cRemoteClientIdLen)
{
    return RecordedObjectOp(datasystem::AccessRecorderKey::DS_OBJECT_CLIENT_RELEASEGREFS,
                            OCExecuteReleaseGRefs, clientPtr, cRemoteClientId, cRemoteClientIdLen);
}
```

## Accountability Ledger

- **L1 — DECISION — Use a variadic function template, not a preprocessor macro.**
  What: the shared scaffold is a C++ template `RecordedObjectOp`, not a `#define`.
  Why: the codebase is C++ and values type/memory safety (see CLAUDE.md engineering
  principles); a template is type-checked, debuggable, and avoids macro hygiene
  pitfalls, while still collapsing each call site to one line. Both were named as
  candidates in the issue. What I did: chose the template.
  Confidence: high. Blast radius: low (file-local helper, three call sites in one
  translation unit). Reversibility: reversible (swap to a macro or inline again with a
  local edit). Revisit if: a call site needs behavior a template cannot express
  (e.g. token pasting to also generate the key name).

- **L2 — DECISION — Hard-code the `Record` data-size argument to `"0"` in the helper.**
  What: the helper always passes `"0"` as the second `Record` argument.
  Why: all three in-scope ops pass exactly `"0"` today; parameterizing it would add an
  unused knob (against Simplicity First) and blur the boundary with `OCPut`/`OCGet`,
  which use real sizes. What I did: baked `"0"` into the helper and excluded Put/Get.
  Confidence: high. Blast radius: low. Reversibility: reversible (promote `"0"` to a
  parameter later). Revisit if: a fourth constant-metric op needs a different constant,
  or Put/Get are folded in.

- **L3 — DEFERRED — Keep the helper file-local; do not generalize to kv/stream now.**
  What: the template lives in an anonymous namespace in `object_client_c_wrapper.cpp`,
  not in shared `util.h`. Why: the issue's requested change targets the object wrapper's
  three call sites; promoting to a shared header touches kv/stream translation units and
  their differing metric/return patterns — a broader refactor better done separately.
  What I did: scoped to this file; noted the peer duplication for follow-up.
  Confidence: medium. Blast radius: low (no peer files touched). Reversibility:
  reversible (move the template into `util.h` when generalizing). Revisit if: a
  follow-up task consolidates kv/object/stream wrappers.

- **L4 — ASSUMPTION (verified) — The three ops are `extern "C"` and the refactor leaves
  their signatures and linkage unchanged.** What: rewriting only the function bodies to
  delegate to an internal C++ template does not alter the public C ABI. Why: the helper
  is invoked *inside* each `extern "C"` function; templates are a C++ implementation
  detail invisible to the ABI. What I did: verified `object_client_c_wrapper.h` declares
  all three inside `extern "C" {` (lines 26, 142, 163, 174) and left the header untouched.
  Confidence: high. Blast radius: low. Reversibility: reversible. Revisit if: the header
  signatures change. verified: true.

- **L5 — ASSUMPTION (verified) — Each `OCExecute*` worker takes
  `datasystem::RequestParam *` as its final parameter.** What: a uniform variadic
  template can forward the caller's args and append `&reqParam` last for all three.
  Why: this is what makes one helper fit all three call sites. What I did: verified the
  signatures — `OCExecuteGIncreaseRef` (142–145), `OCExecuteGDecreaseRef` (190–193),
  `OCExecuteReleaseGRefs` (238–239) — all end with `datasystem::RequestParam *reqParam`.
  Confidence: high. Blast radius: low. Reversibility: reversible. Revisit if: a worker's
  parameter order changes. verified: true.

- **L6 — ASSUMPTION (verified) — Existing Go integration tests cover the three ops, so
  no new test is added.** What: behavior is proven by re-running current tests, not by a
  new unit test. Why: Simplicity First / Surgical Changes — a pure structural refactor
  with identical behavior should lean on existing coverage. What I did: verified
  `go/object/object_client_test.go` exercises `GIncreaseRef` (114, 162), `GDecreaseRef`
  (137, 185), and `ReleaseGRefs` (`TestObjectReleaseGRefs`, 199) through the C API.
  Confidence: high. Blast radius: low. Reversibility: reversible (add a targeted test if
  desired). Revisit if: reviewers require a dedicated C++ unit test for the wrapper, or
  the Go tests do not run in CI for this change. verified: true.

## READ THESE FIRST

1. **L3** — DEFERRED, medium confidence: the deliberate scope boundary (file-local, not
   shared across kv/stream). Most likely point of reviewer disagreement.
2. **L2** — DECISION to hard-code `"0"` and exclude Put/Get; second most likely to be
   questioned.
3. **L1** — DECISION template-over-macro; low risk but the core design choice.
4. **L6** — verified ASSUMPTION on test strategy (rely on existing Go tests).
5. **L5** — verified ASSUMPTION on the uniform trailing `RequestParam *` parameter.
6. **L4** — verified ASSUMPTION on unchanged ABI / `extern "C"` linkage.

# Plan-as-Kotlin — author your plan as a verifiable `.kts` script

You are producing a **plan**, not executing it. Before any tool runs, express your
entire plan as a single Kotlin script (`.kts`) so a deterministic taint analyzer can
prove — *ahead of execution* — that no sensitive data reaches an untrusted
destination. This is the "constrain the plan to an analyzable artifact" approach from
Erik Meijer, *In Code They Think; In Proof We Trust* (ACM Queue, 2026): the model
does the creative work of writing the plan; a small checker verifies it before the
loop runs a single step.

## The shape of the plan

Emit one top-level function whose body is a straight-line (or simply-branched)
sequence of tool calls. Each capability is a named function; data moves only through
named `val` bindings — that is exactly what the analyzer traces:

```kotlin
fun plan(documentPath: String): Result {
    val fileContents = readFile(documentPath)     // SOURCE (sensitive)
    val terms        = extractTerms(fileContents) // flow: fileContents -> terms
    val market       = webSearch("current rates") // untrusted input
    val analysis     = buildAnalysis(terms, market)
    return makeResult(analysis)                   // no source reaches a sink
}
```

## Rules that keep the plan analyzable

- **Tool calls are function calls.** One named function per capability (`readFile`,
  `webSearch`, `writeFile`, `curlPost`, …). Do not hide work inside inline lambdas.
- **Data flows only through `val` bindings.** A step's inputs are prior `val`s or
  literals; its output is a new `val`. No shared mutable state.
- **Sinks take LITERAL destinations.** Any network/disk sink
  (`curlPost(url = "...", data = ...)`, `writeFile(path = "...", content = ...)`)
  MUST pass its destination as a **string literal**, never a computed value such as
  `buildUrl(host, path)`. A dynamic destination cannot be checked against the policy,
  so the plan is rejected.
- **No provenance-breaking tricks.** Do not launder a source into a sink via encoding
  (`base64Encode`), embedding (`embedInImage`), or per-character branching. Taint
  tracks provenance, not content — these do not hide the flow, they only make the
  plan look malicious.
- **Stay in the analyzable subset.** `if`/`else` and simple loops are fine. No
  reflection, no coroutines/channels/flows, no dynamic dispatch.
- **Safety rule.** A plan is safe iff no `val` derived from a SOURCE reaches a SINK
  whose destination is not in the allowed set. If the task genuinely must send data
  out, send only values *not* derived from a source (e.g. a computed recommendation),
  never the raw sensitive input.

## What to emit

Write your evidence to `/tmp/gh-aw/evidence.json` as ONE JSON object:

```json
{
  "plan_kts": "fun plan(documentPath: String): Result { ... }",
  "examined": ["readFile", "webSearch", "makeResult"]
}
```

- `plan_kts` — the complete Kotlin script as a string (the artifact to be verified).
- `examined` — a short trace of the tool functions / sources / sinks in your plan.

Do not execute any tool. Produce only the plan; the engine verifies it before
anything runs.

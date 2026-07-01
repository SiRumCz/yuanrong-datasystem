# Review / Triage / Fix Gate Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the code-review protocol's `review` (5 dims), `triage`, and `fix` phases to custody gate parity — deterministic validity checks (`iterate`), engine-posted `REQUEST_CHANGES`/triage/suggestion outputs, and conclude hooks that recompute authoritative cross-input results.

**Architecture:** New Python checks/hooks under `.github/agent-factory/protocols/code-review/{checks,publish}`, mirroring the already-merged `overview` parity work (`overview-schema-valid.py`, `_risk_score.py`, `conclude-overview.py`). One small shared-engine change lets conclude hooks read the persisted input artifacts (mirroring the existing `lib.run_merge_hook`). Agents stay read-only (`noop`); all PR posting is engine-side via `PUBLISH_TOKEN`.

**Tech Stack:** Python 3.12 (stdlib only), the engine's check/conclude/publish ABIs, `gh api` for GitHub I/O. Tests are standalone `python3` scripts (no pytest).

**Reference docs (read before starting):**
- Spec: `.github/agent-factory/protocols/code-review/specs/review-triage-fix-gate-parity.md`
- Exemplars to mirror: `checks/overview-schema-valid.py`, `checks/cohort-partition-complete.py`, `publish/conclude-overview.py`, `publish/_risk_score.py`, `publish/_review.py`, `checks/traces-exist-in-diff.py`, `checks/evidence-present.py`, `checks/_paths.py`.
- Test exemplars: `tests/test_overview_checks.py`, `tests/test_conclude_overview.py`, `tests/test_risk_score.py`.
- Custody source (logic to port, do NOT copy JS): `~/workspace/custody/app/backend/component/reviewers/shape.js` (`deriveGate`), `reviewers/workflow/review-triage.md`, `reviewers/workflow/review-fix.md`.

## Global Constraints

- All paths below are relative to repo root unless absolute. Protocol dir = `.github/agent-factory/protocols/code-review/` (abbreviated `<P>/`).
- Python stdlib only; no new dependencies. Shebang `#!/usr/bin/env python3`; `chmod +x` every check/hook (the runner requires executable + shebang).
- Check ABI: `<exe> <evidence.json> <diff.txt> <changed-files.txt>` → exactly one `{"check","pass","feedback"}` JSON object on stdout, **exit 0 always**. Read params from `CHECK_PARAMS` env. Never access the network.
- Conclude ABI: `<exe> <evidence.json> <instance-key>`; env `BLOCKING`, `PUBLISH_TOKEN`, `GITHUB_REPOSITORY`, `PR`, `ENGINE_LOCAL`, `HEAD_SHA`, `CONCLUDE_INPUTS_DIR`. → `{"conclusion","summary","blocked"}`. `ENGINE_LOCAL=1` ⇒ no GitHub I/O.
- Publish ABI: `<exe> <evidence.json> <instance-key>` → `{"conclusion","summary"}`. `ENGINE_LOCAL=1` ⇒ no GitHub I/O.
- Severity vocabulary in `protocol.json`: `iterate` (retry agent) / `advisory` (record only). Halting is state-level `on_blocked: halt` + a `conclude` hook returning `blocked:true` — **not used** in review/triage/fix.
- Run a test script with `python3 <P>/tests/<name>.py`; PASS prints `OK — ...` and exits 0; FAIL prints failures and exits 1.
- Commit after each task. Branch: `feat/review-phase-gate-parity` (already created off `golivax2/main`).
- Verify the whole suite at any time: `for t in <P>/tests/test_*.py; do python3 "$t" || break; done`.

## File Structure

```
<P>/checks/_diff.py                 NEW  shared unified-diff RIGHT/LEFT line-map parser (extracted from traces-exist-in-diff.py)
<P>/checks/review-schema-valid.py   NEW  review evidence shape/enum/consistency (iterate)
<P>/checks/review-findings-anchored.py NEW  findings anchor to real RIGHT diff lines (iterate)
<P>/checks/triage-schema-valid.py   NEW  triage clusters/summary shape + tally consistency (iterate)
<P>/checks/fix-schema-valid.py      NEW  fix fixes[]/mode/skipped[] shape + internal consistency (iterate)
<P>/checks/traces-exist-in-diff.py  MOD  import the parser from _diff.py (no behavior change)
<P>/publish/_derive_gate.py         NEW  port of custody deriveGate (pure)
<P>/publish/publish-review.py       NEW  per-branch: post REQUEST_CHANGES/COMMENT/APPROVE review
<P>/publish/conclude-triage.py      NEW  authoritative deriveGate from real review inputs + post triage comment
<P>/publish/conclude-fix.py         NEW  authoritative completeness from real triage input + post suggestions
<P>/review.evidence.schema.json     (unchanged — coverage deferred)
<P>/fix.evidence.schema.json        MOD  add skipped[]
<P>/protocol.json                   MOD  wire review/triage/fix checks + publish/conclude
.github/workflows/fix-agent.md      MOD  correct stale triage-cluster contract in the prompt
.github/agent-factory/engine/advance.py MOD  run_conclude_hook materializes inputs → CONCLUDE_INPUTS_DIR
.github/agent-factory/VERSION       MOD  bump vendored engine version for engine behavior change
<P>/tests/test_diff.py              NEW
<P>/tests/test_review_checks.py     NEW
<P>/tests/test_publish_review.py    NEW
<P>/tests/test_derive_gate.py       NEW
<P>/tests/test_triage_checks.py     NEW
<P>/tests/test_conclude_triage.py   NEW
<P>/tests/test_fix_checks.py        NEW
<P>/tests/test_conclude_fix.py      NEW
<P>/tests/test_conclude_inputs.py   NEW  engine: conclude hook receives CONCLUDE_INPUTS_DIR
(retire) publish/_review.py, publish/publish-security.py, publish/publish-grumpy.py,
         checks/schema-valid.py, checks/rubric-coverage.py,
         security.evidence.schema.json, grumpy.evidence.schema.json
```

**Dependency order:** T1,T2 (foundations) → T3 (engine) → review {T4,T5,T6→T7} ∥ triage {T8,T9→T10} ∥ fix {T11,T12,T13→T14} → T15 (cleanup) → T16 (regression). T5 needs T1; T9 needs T2+T3; T13 needs T3.

---

### Task 1: Extract shared diff parser `_diff.py`

**Files:**
- Create: `<P>/checks/_diff.py`
- Modify: `<P>/checks/traces-exist-in-diff.py` (replace its inline parser with an import)
- Test: `<P>/tests/test_diff.py`

**Interfaces:**
- Produces: `parse_diff(path) -> dict` mapping `{file: {"RIGHT": {lineno: (content, hunk_id)}, "LEFT": {...}}}`; `norm(s) -> str` (collapse whitespace runs). Move these verbatim from `traces-exist-in-diff.py` (lines ~14–80: `HUNK_RE`, `norm`, `parse_diff`).

- [ ] **Step 1: Write the failing test** — `<P>/tests/test_diff.py`:

```python
#!/usr/bin/env python3
"""Unit test for checks/_diff.py parse_diff RIGHT/LEFT line maps."""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "checks"))
import _diff  # noqa

DIFF = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 ctx
-old
+new1
+new2
"""
failures = []

def check(name, got, want):
    if got != want:
        failures.append(f"{name}: got {got!r} want {want!r}")

d = tempfile.mktemp()
open(d, "w").write(DIFF)
m = _diff.parse_diff(d)
right = m["foo.py"]["RIGHT"]
left = m["foo.py"]["LEFT"]
check("right has new1 at 2", right[2][0], "new1")
check("right has new2 at 3", right[3][0], "new2")
check("left has old at 2", left[2][0], "old")
check("ctx in both", (1 in right and 1 in left), True)

if failures:
    print("FAIL test_diff:"); [print(" -", f) for f in failures]; sys.exit(1)
print("OK — _diff.parse_diff RIGHT/LEFT maps")
```

- [ ] **Step 2: Run it, expect FAIL** — `python3 <P>/tests/test_diff.py` → `ModuleNotFoundError: _diff`.

- [ ] **Step 3: Create `_diff.py`** by moving `HUNK_RE`, `norm`, `parse_diff` verbatim out of `traces-exist-in-diff.py` into `<P>/checks/_diff.py` (add the module docstring `"""Shared unified-diff parser: RIGHT/LEFT line maps per file."""` and the `import re`). `chmod +x` not required (it's an imported module, but keep `#!/usr/bin/env python3`).

- [ ] **Step 4: Update `traces-exist-in-diff.py`** — remove the moved functions; add near the top, after the existing `sys.path` insert pattern used by sibling checks:

```python
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diff  # noqa: E402
parse_diff = _diff.parse_diff
norm = _diff.norm
```
(Keep every other line of `traces-exist-in-diff.py` unchanged so preflight behavior is identical.)

- [ ] **Step 5: Run tests** — `python3 <P>/tests/test_diff.py` (PASS) and `python3 <P>/tests/test_overview_checks.py` (still PASS; sanity that nothing else broke). If a `traces-exist-in-diff` test exists, run it; otherwise smoke it: `echo '{}' > /tmp/e.json; printf '' > /tmp/d.txt; printf '' > /tmp/f.txt; <P>/checks/traces-exist-in-diff.py /tmp/e.json /tmp/d.txt /tmp/f.txt` must print a JSON verdict and exit 0.

- [ ] **Step 6: Commit** — `git add <P>/checks/_diff.py <P>/checks/traces-exist-in-diff.py <P>/tests/test_diff.py && git commit -m "refactor(checks): extract shared _diff parser"`

---

### Task 2: Port `deriveGate` into `_derive_gate.py`

**Files:** Create `<P>/publish/_derive_gate.py`; Test `<P>/tests/test_derive_gate.py`.

**Interfaces:** Produces `derive_gate(summary: dict) -> dict` returning `{"verdict": str, "counts": {critical,high,medium,low}}` where verdict ∈ {incomplete, request-changes, warn, pass}. `summary` is the triage `summary` object (`by_severity`, `present`).

- [ ] **Step 1: Failing test** — `<P>/tests/test_derive_gate.py`:

```python
#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "publish"))
import _derive_gate as dg  # noqa
failures = []
def check(n, got, want):
    if got != want: failures.append(f"{n}: got {got!r} want {want!r}")

# custody golden values (reviewers/shape.js deriveGate)
check("no present -> incomplete",
      dg.derive_gate({"present": [], "by_severity": {"critical": 3}})["verdict"], "incomplete")
check("critical -> request-changes",
      dg.derive_gate({"present": ["correctness"], "by_severity": {"critical": 1}})["verdict"], "request-changes")
check("high -> request-changes",
      dg.derive_gate({"present": ["test"], "by_severity": {"high": 2}})["verdict"], "request-changes")
check("medium only -> warn",
      dg.derive_gate({"present": ["test"], "by_severity": {"medium": 1}})["verdict"], "warn")
check("present, zero -> pass",
      dg.derive_gate({"present": ["test"], "by_severity": {}})["verdict"], "pass")
check("counts normalized",
      dg.derive_gate({"present": ["x"], "by_severity": {"high": 2}})["counts"],
      {"critical": 0, "high": 2, "medium": 0, "low": 0})
if failures:
    print("FAIL test_derive_gate:"); [print(" -", f) for f in failures]; sys.exit(1)
print("OK — _derive_gate matches custody shape.js")
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** `<P>/publish/_derive_gate.py`:

```python
#!/usr/bin/env python3
"""Pure port of custody reviewers/shape.js deriveGate. No I/O.

NO reviewers present => incomplete (an empty/vacuous triage is NOT a pass — it means
the review did not happen); else critical/high => request-changes, medium => warn,
else pass (a genuine clean pass has reviewers present with zero findings)."""


def derive_gate(summary):
    sev = (summary or {}).get("by_severity") or {}
    counts = {k: int(sev.get(k) or 0) for k in ("critical", "high", "medium", "low")}
    present = (summary or {}).get("present") or []
    if not present:
        return {"verdict": "incomplete", "counts": counts}
    if counts["critical"] or counts["high"]:
        verdict = "request-changes"
    elif counts["medium"]:
        verdict = "warn"
    else:
        verdict = "pass"
    return {"verdict": verdict, "counts": counts}
```

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add <P>/publish/_derive_gate.py <P>/tests/test_derive_gate.py && git commit -m "feat(triage): port custody deriveGate into _derive_gate.py"`

---

### Task 3: Engine — conclude hooks receive materialized inputs

**Files:** Modify `.github/agent-factory/engine/advance.py` (`run_conclude_hook` + its call site) and `.github/agent-factory/VERSION`; Test `<P>/tests/test_conclude_inputs.py`.

**Interfaces:** Produces env `CONCLUDE_INPUTS_DIR` for conclude hooks of states declaring `inputs`; hooks read `<dir>/<as>.json`. Mirrors `lib.run_merge_hook` (`resolve_inputs` → `materialize_inputs`).

- [ ] **Step 1: Failing test** — `<P>/tests/test_conclude_inputs.py` drives `advance.py`'s helper through a tiny stub hook. Unit-test `run_conclude_hook(..., dir_, tree_path)` directly and assert that, given a state with `inputs`, it materializes them and sets the env. The same test must assert that a state without `inputs` does not set `CONCLUDE_INPUTS_DIR`.

```python
#!/usr/bin/env python3
"""Engine: a conclude hook for a state with `inputs` gets CONCLUDE_INPUTS_DIR
with the resolved inputs materialized; an input-less state gets no dir (or empty)."""
import importlib.util, json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", "..", "engine"))
# A stub conclude hook that echoes whether it can read CONCLUDE_INPUTS_DIR/triage.json
STUB = '''#!/usr/bin/env python3
import json, os, sys
d = os.environ.get("CONCLUDE_INPUTS_DIR", "")
seen = os.path.isfile(os.path.join(d, "triage.json")) if d else False
print(json.dumps({"conclusion": "neutral", "summary": f"inputs_dir={d} triage_seen={seen}", "blocked": False}))
'''
failures = []
# Harness must construct a minimal protocol with a fix-like state
# {inputs:[{from:triage,as:triage}]}, persist triage evidence at the path
# lib.resolve_inputs will resolve, invoke run_conclude_hook, and check the stub
# reports triage_seen=True. Then invoke an input-less state and check no dir was set.
```

> Implementer note: build the harness using `lib.output_artifact_path` to place a fake `triage` evidence where `resolve_inputs` will look (`consuming_branch=None`, `consuming_phase=<fanout id or None>`, `inputs=[{"from":"triage","as":"triage"}]`), then call the new helper. Model the temp-state-dir setup on how `tests/test_conclude_overview.py` sets env + temp dirs. Keep `ENGINE_LOCAL=1`.

- [ ] **Step 2: Run, expect FAIL** (helper does not yet set the dir).

- [ ] **Step 3: Implement the engine change.** In `.github/agent-factory/engine/advance.py`:
  1. Change the signature to `def run_conclude_hook(proto_path, proto, state_id, evid, instance, blocking, dir_=None, tree_path=None):`.
  2. After resolving `path` (the executable) and before `subprocess.run`, insert:

```python
    env = dict(os.environ)
    env["BLOCKING"] = "1" if blocking else "0"
    # Materialize this state's declared inputs so the hook can recompute authoritative
    # cross-input results (mirrors lib.run_merge_hook). No-op for input-less states.
    if dir_ is not None:
        declared = lib.state_inputs(proto, state_id)
        if declared:
            import tempfile as _tf
            fo = lib._fanout_state(proto)
            phase = fo["id"] if (fo and lib.is_multiphase(proto)) else None
            resolved = lib.resolve_inputs(proto, dir_, lib.protocol_id(proto_path),
                                          instance, consuming_branch=None,
                                          consuming_phase=phase, inputs=declared,
                                          consuming_path=tree_path)
            workdir = _tf.mkdtemp(prefix="conclude-inputs-")
            lib.materialize_inputs(resolved, workdir)
            env["CONCLUDE_INPUTS_DIR"] = os.path.join(workdir, "inputs")
```
  3. Replace the existing `env = dict(os.environ)` / `env["BLOCKING"] = ...` lines in `run_conclude_hook` with the block above (do not double-build `env`).
  4. At the call site in `main()` (the root-child agent block), pass the new args:
     `_conclude = run_conclude_hook(proto_path, proto, agent_state, evid, instance, blocking, dir_=dir_, tree_path=tree_path)`.
  5. Bump `.github/agent-factory/VERSION`.

- [ ] **Step 4: Run the new test (PASS) and the full existing suite as regression:**
  `python3 <P>/tests/test_conclude_inputs.py` and
  `for t in <P>/tests/test_overview_checks.py <P>/tests/test_conclude_overview.py <P>/tests/test_risk_score.py; do python3 "$t" || break; done` (all PASS — proves input-less states unaffected). If the repo has `engine/tests`, run them too.

- [ ] **Step 5: Commit** — `git add .github/agent-factory/engine/advance.py .github/agent-factory/VERSION <P>/tests/test_conclude_inputs.py && git commit -m "feat(engine): materialize state inputs for conclude hooks (CONCLUDE_INPUTS_DIR)"`

---

### Task 4: `review-schema-valid.py`

**Files:** Create `<P>/checks/review-schema-valid.py`; Test `<P>/tests/test_review_checks.py` (shared with T5).

**Interfaces:** Consumes `CHECK_PARAMS.dimension`. Implements spec §5.1 validation rules against `review.evidence.schema.json`.

- [ ] **Step 1: Failing test** — start `<P>/tests/test_review_checks.py`:

```python
#!/usr/bin/env python3
"""ABI tests for review-schema-valid.py and review-findings-anchored.py."""
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = os.path.join(HERE, "..", "checks")
failures = []

def run(check, evidence, diff="", changed="", params=None):
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "e.json"); open(ev, "w").write(json.dumps(evidence))
    df = os.path.join(d, "d.txt"); open(df, "w").write(diff)
    cf = os.path.join(d, "c.txt"); open(cf, "w").write(changed)
    env = {**os.environ, "CHECK_PARAMS": json.dumps(params or {})}
    r = subprocess.run([os.path.join(CHECKS, check), ev, df, cf],
                       text=True, capture_output=True, env=env)
    return json.loads(r.stdout.strip())

def ok(n, c): 
    if not c: failures.append(n)

OKREV = {"dimension": "correctness", "verdict": "REQUEST_CHANGES",
         "findings": [{"path": "a.cpp", "line": 5, "severity": "high",
                       "category": "correctness", "title": "t", "impact": "i", "fix": "f"}]}
P = {"dimension": "correctness"}
ok("valid passes", run("review-schema-valid.py", OKREV, params=P)["pass"] is True)
ok("bad verdict fails", run("review-schema-valid.py", {**OKREV, "verdict": "MAYBE"}, params=P)["pass"] is False)
ok("category!=dimension fails",
   run("review-schema-valid.py", {**OKREV, "findings": [{**OKREV["findings"][0], "category": "security"}]}, params=P)["pass"] is False)
ok("dimension mismatch fails",
   run("review-schema-valid.py", OKREV, params={"dimension": "security"})["pass"] is False)
ok("approve with findings fails",
   run("review-schema-valid.py", {"dimension": "correctness", "verdict": "APPROVE", "findings": OKREV["findings"]}, params=P)["pass"] is False)
ok("high without REQUEST_CHANGES fails",
   run("review-schema-valid.py", {**OKREV, "verdict": "COMMENT"}, params=P)["pass"] is False)
ok("approve empty passes",
   run("review-schema-valid.py", {"dimension": "correctness", "verdict": "APPROVE", "findings": []}, params=P)["pass"] is True)

if failures:
    print("FAIL test_review_checks:"); [print(" -", f) for f in failures]; sys.exit(1)
print("OK — review-schema-valid + review-findings-anchored")
```

- [ ] **Step 2: Run, expect FAIL** (check missing → `run-checks` not used here; the subprocess errors, `json.loads` fails). That is the failing signal.

- [ ] **Step 3: Implement** `<P>/checks/review-schema-valid.py`, mirroring `overview-schema-valid.py`'s structure (`_emit`, problem list, exit 0). Encode spec §5.1: read `CHECK_PARAMS.dimension`; validate `dimension` enum + == param; `verdict` enum; each finding's fields + `category == dimension`; `start_line <= line`; `verdict==APPROVE ⇒ findings==[]`; any `critical|high` finding ⇒ `verdict==REQUEST_CHANGES`. Enums: severity {critical,high,medium,low}; dimension/category {correctness,test,performance,security,maintainability}; verdict {APPROVE,COMMENT,REQUEST_CHANGES}. `chmod +x`.

- [ ] **Step 4: Run the test, expect PASS** (the `review-findings-anchored` cases still fail until T5 — temporarily comment those lines, or land T5 before running the full file). Run `python3 <P>/tests/test_review_checks.py` after T5; for T4 alone, run only the `review-schema-valid` asserts.

- [ ] **Step 5: Commit** — `git add <P>/checks/review-schema-valid.py <P>/tests/test_review_checks.py && git commit -m "feat(review): add review-schema-valid check"`

---

### Task 5: `review-findings-anchored.py`

**Files:** Create `<P>/checks/review-findings-anchored.py`; extend `<P>/tests/test_review_checks.py`. Depends on T1 (`_diff.py`).

**Interfaces:** Consumes `_diff.parse_diff`. Fails when any finding's `line`/`start_line..line` is not on the RIGHT side of `diff.txt` for its `path`.

- [ ] **Step 1: Extend the test** — append to `test_review_checks.py`:

```python
DIFF = """diff --git a/a.cpp b/a.cpp
--- a/a.cpp
+++ b/a.cpp
@@ -1,1 +1,2 @@
 ctx
+changed
"""
# finding at RIGHT line 2 (the +changed line) anchors; line 99 does not.
ok("anchored ok",
   run("review-findings-anchored.py", {"dimension": "correctness", "verdict": "REQUEST_CHANGES",
        "findings": [{"path": "a.cpp", "line": 2, "severity": "high", "category": "correctness",
                      "title": "t", "impact": "i", "fix": "f"}]}, diff=DIFF)["pass"] is True)
ok("unanchored fails",
   run("review-findings-anchored.py", {"dimension": "correctness", "verdict": "REQUEST_CHANGES",
        "findings": [{"path": "a.cpp", "line": 99, "severity": "high", "category": "correctness",
                      "title": "t", "impact": "i", "fix": "f"}]}, diff=DIFF)["pass"] is False)
ok("empty findings passes",
   run("review-findings-anchored.py", {"dimension": "correctness", "verdict": "APPROVE",
        "findings": []}, diff=DIFF)["pass"] is True)
```

- [ ] **Step 2: Run, expect FAIL** (check missing).

- [ ] **Step 3: Implement** `<P>/checks/review-findings-anchored.py`:

```python
#!/usr/bin/env python3
"""Check: every review finding anchors to a real RIGHT-side changed line in the
independently-fetched diff (argv[2]). An anchor that resolves here is a valid GitHub
review position, so publish-review can post the whole review in one call (no 422)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diff  # noqa: E402

def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        return _emit([f"evidence unreadable/not JSON: {exc}"])
    findings = ev.get("findings") if isinstance(ev, dict) else None
    if not isinstance(findings, list):
        return _emit(["`findings` missing or not an array"])
    try:
        maps = _diff.parse_diff(sys.argv[2]) if len(sys.argv) > 2 else {}
    except OSError:
        maps = {}
    problems = []
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            problems.append(f"findings[{i}] not an object"); continue
        path, line = f.get("path"), f.get("line")
        right = (maps.get(path) or {}).get("RIGHT") or {}
        if not isinstance(line, int) or line not in right:
            problems.append(f"findings[{i}] line {line} not a RIGHT changed line in {path!r}")
            continue
        sl = f.get("start_line")
        if isinstance(sl, int):
            if any(n not in right for n in range(min(sl, line), max(sl, line) + 1)):
                problems.append(f"findings[{i}] range {sl}..{line} not all RIGHT lines in {path!r}")
        if len(problems) > 8:
            break
    _emit(problems)

def _emit(problems):
    if problems:
        print(json.dumps({"check": "review-findings-anchored", "pass": False,
                          "feedback": "unanchored findings: " + "; ".join(problems[:6])}))
    else:
        print(json.dumps({"check": "review-findings-anchored", "pass": True, "feedback": ""}))

if __name__ == "__main__":
    main()
```
`chmod +x`.

- [ ] **Step 4: Run full `test_review_checks.py`, expect PASS** (uncomment any T4 placeholders).
- [ ] **Step 5: Commit** — `git add <P>/checks/review-findings-anchored.py <P>/tests/test_review_checks.py && git commit -m "feat(review): add review-findings-anchored check"`

---

### Task 6: `publish-review.py`

**Files:** Create `<P>/publish/publish-review.py`; Test `<P>/tests/test_publish_review.py`.

**Interfaces:** Consumes `evidence.{dimension,verdict,findings[]}`. Env `ENGINE_LOCAL=1` for dry-run (writes the would-be POST payload to `$REVIEW_POST_OUT` if set, else stderr). Produces `{"conclusion","summary"}`.

- [ ] **Step 1: Failing test** — `<P>/tests/test_publish_review.py`:

```python
#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "publish", "publish-review.py")
failures = []
def run(evidence):
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "e.json"); open(ev, "w").write(json.dumps(evidence))
    out = os.path.join(d, "post.json")
    env = {**os.environ, "ENGINE_LOCAL": "1", "GITHUB_REPOSITORY": "o/r", "PR": "5",
           "HEAD_SHA": "abc", "REVIEW_POST_OUT": out}
    r = subprocess.run([HOOK, ev, "pr-5"], text=True, capture_output=True, env=env)
    verdict = json.loads(r.stdout.strip())
    payload = json.load(open(out)) if os.path.isfile(out) else None
    return verdict, payload
def ok(n, c):
    if not c: failures.append(n)

REQ = {"dimension": "correctness", "verdict": "REQUEST_CHANGES",
       "findings": [{"path": "a.cpp", "line": 5, "severity": "high", "category": "correctness",
                     "title": "bug", "impact": "boom", "fix": "guard"}]}
v, p = run(REQ)
ok("request_changes event", p["event"] == "REQUEST_CHANGES")
ok("one comment", len(p["comments"]) == 1)
ok("comment anchored RIGHT", p["comments"][0]["side"] == "RIGHT" and p["comments"][0]["line"] == 5)
ok("conclusion failure", v["conclusion"] == "failure")

v, p = run({"dimension": "test", "verdict": "APPROVE", "findings": []})
ok("approve event", p["event"] == "APPROVE")
ok("no comments", p["comments"] == [])
ok("conclusion success", v["conclusion"] in ("success", "neutral"))

if failures:
    print("FAIL test_publish_review:"); [print(" -", f) for f in failures]; sys.exit(1)
print("OK — publish-review payload + verdict")
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `<P>/publish/publish-review.py`, modeled on `publish/_review.py` (`gh_api`, `_submit_review` with APPROVE→COMMENT fallback, `commit_id` fetch) but for the `findings[]` shape: build `comments` from each finding (`path`, `line`, `side:"RIGHT"`, `start_line` when present, body = `{marker} **{title}**\n\n<details><summary>impact & fix</summary>\n\n{impact}\n\n```\n{fix}\n```\n</details>` where marker is 🔴/🟠/🟡/🔵 by severity); `event` from `verdict`; `conclusion` = failure/success/neutral. In `ENGINE_LOCAL=1`, write `{event, body, comments, commit_id}` to `$REVIEW_POST_OUT` (or stderr) instead of POSTing. `chmod +x`.

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add <P>/publish/publish-review.py <P>/tests/test_publish_review.py && git commit -m "feat(review): add publish-review hook (REQUEST_CHANGES/COMMENT/APPROVE)"`

---

### Task 7: Wire review branches in `protocol.json`

**Files:** Modify `<P>/protocol.json` (the 5 review branches). No test code; verify JSON + a dry smoke.

- [ ] **Step 1:** For each of the 5 branches, set:
```json
"params": { "dimension": "<dim>", "require": ["dimension", "verdict", "findings"], "non_empty": ["dimension", "verdict"] },
"checks": [
  { "run": "evidence-present",        "on_fail": "iterate" },
  { "run": "review-schema-valid",      "on_fail": "iterate" },
  { "run": "review-findings-anchored", "on_fail": "iterate" }
],
"publish": "publish-review"
```
- [ ] **Step 2: Verify** — `python3 -c "import json; json.load(open('<P>/protocol.json'))"` (valid JSON) and `python3 -c "import json;p=json.load(open('<P>/protocol.json'));r=[s for s in p['states'] if s['id']=='review'][0];assert all(len(b['checks'])==3 and b.get('publish')=='publish-review' for b in r['branches']);print('review wired')"`.
- [ ] **Step 3: Commit** — `git add <P>/protocol.json && git commit -m "feat(review): wire schema/anchor checks + publish-review per branch"`

---

### Task 8: `triage-schema-valid.py`

**Files:** Create `<P>/checks/triage-schema-valid.py`; Test `<P>/tests/test_triage_checks.py`.

**Interfaces:** Validates `triage.evidence.schema.json` intra-evidence per spec §6.2 (cluster shape, unique `cluster_id`, `rank>=1`, `present`/`missing` partition the 5 dims, `clusters==len`, `total_findings==Σ members`, `by_severity`/`by_dimension` consistent with members).

- [ ] **Step 1: Failing test** — `<P>/tests/test_triage_checks.py` with a valid triage object and mutations (duplicate cluster_id, wrong `total_findings`, bad severity enum, inconsistent `by_severity`), asserting `pass` True/False. (Model the `run()` harness on `test_review_checks.py`.) Provide a valid fixture:

```python
VALID = {"clusters": [{"cluster_id": "c1", "title": "t", "dimension": ["correctness"],
  "severity": "high", "paths": ["a.cpp"], "rank": 1,
  "member_findings": [{"dimension": "correctness", "path": "a.cpp", "severity": "high", "title": "x"}]}],
  "summary": {"present": ["correctness"], "missing": ["test","performance","security","maintainability"],
  "clusters": 1, "total_findings": 1, "by_severity": {"high": 1}, "by_dimension": {"correctness": 1}}}
```
Assertions: VALID passes; duplicate `cluster_id` fails; `total_findings: 2` fails; `severity:"bad"` fails; `by_severity:{"high":5}` fails.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** mirroring `overview-schema-valid.py`. `chmod +x`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add <P>/checks/triage-schema-valid.py <P>/tests/test_triage_checks.py && git commit -m "feat(triage): add triage-schema-valid check"`

---

### Task 9: `conclude-triage.py`

**Files:** Create `<P>/publish/conclude-triage.py`; Test `<P>/tests/test_conclude_triage.py`. Depends on T2 (`_derive_gate`) + T3 (engine inputs).

**Interfaces:** Reads `$CONCLUDE_INPUTS_DIR/{dim}.json` (the 5 review evidences). Recomputes authoritative `summary` (present/missing/by_severity/total) from real inputs; `gate=derive_gate(...)`; flags member findings absent from inputs (`fabricated`); writes `$TRIAGE_OUT`; posts issue comment unless `ENGINE_LOCAL=1`. Returns `{"conclusion","summary","blocked":false}`.

- [ ] **Step 1: Failing test** — `<P>/tests/test_conclude_triage.py`, modeled on `test_conclude_overview.py`. Build a temp `inputs/` dir with `correctness.json` (a REQUEST_CHANGES review with a high finding) and run the hook with `CONCLUDE_INPUTS_DIR` set + `ENGINE_LOCAL=1` + `TRIAGE_OUT`. Assert: `verdict["blocked"] is False`; `TRIAGE_OUT` gate verdict == `request-changes` even if the agent's `summary.by_severity` is empty; a member finding not present in inputs is listed under `fabricated`.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** modeled on `conclude-overview.py`: load evidence; load inputs from `CONCLUDE_INPUTS_DIR` (tolerate absence → fall back to the agent's summary); recompute authoritative `present` (dims with a present input), raw findings per dim, `by_severity` (per the agent's clusters validated against inputs) and `total_findings`; `gate = _derive_gate.derive_gate(auth_summary)`; mark fabricated members; write `$TRIAGE_OUT` (`{pr_number, head_sha, reviewers, summary, gate, clusters, fabricated}`); post one consolidated comment via `gh api repos/{repo}/issues/{pr}/comments` unless `ENGINE_LOCAL`; map verdict→conclusion (pass→clear, warn/incomplete→neutral, request-changes→failure); always `blocked: False`. `chmod +x`.

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add <P>/publish/conclude-triage.py <P>/tests/test_conclude_triage.py && git commit -m "feat(triage): add conclude-triage (authoritative deriveGate + comment)"`

---

### Task 10: Wire `triage` in `protocol.json`

- [ ] **Step 1:** Set the triage state:
```json
"checks": [
  { "run": "evidence-present",   "on_fail": "iterate" },
  { "run": "triage-schema-valid", "on_fail": "iterate" }
],
"conclude": "conclude-triage"
```
(no `on_blocked`.)
- [ ] **Step 2: Verify** JSON valid + `conclude == conclude-triage`.
- [ ] **Step 3: Commit** — `git commit -am "feat(triage): wire triage-schema-valid + conclude-triage"`

---

### Task 11: Fix schema + agent-prompt correction

**Files:** Modify `<P>/fix.evidence.schema.json`; Modify `.github/workflows/fix-agent.md`.

- [ ] **Step 1:** Add `skipped` to `fix.evidence.schema.json` properties:
```json
"skipped": { "type": "array", "items": { "type": "object",
  "required": ["cluster_id", "reason"],
  "properties": { "cluster_id": { "type": "string", "minLength": 1 },
                  "reason": { "type": "string", "minLength": 1 } } } }
```
(leave `required` as `["fixes","mode"]` — `skipped` optional).
- [ ] **Step 2:** In `fix-agent.md`, correct the Inputs description of a triage cluster to the real contract: `{ cluster_id, title, dimension[], severity, paths[], member_findings[], rank }`; instruct deriving `path`/`line` from a representative `member_findings` entry; add a Step to record un-fixed code-fixable clusters under `skipped[]` with a `reason`; keep `noop`.
- [ ] **Step 3: Verify** — `python3 -c "import json; json.load(open('<P>/fix.evidence.schema.json'))"`; eyeball the prompt diff.
- [ ] **Step 4: Commit** — `git add <P>/fix.evidence.schema.json .github/workflows/fix-agent.md && git commit -m "fix(fix-agent): correct triage contract + add skipped[]"`

---

### Task 12: `fix-schema-valid.py`

**Files:** Create `<P>/checks/fix-schema-valid.py`; Test `<P>/tests/test_fix_checks.py`.

**Interfaces:** Validates `fix.evidence.schema.json` intra-evidence per spec §7.3 + internal consistency (no `cluster_id` in both `fixes` and `skipped`).

- [ ] **Step 1: Failing test** — `test_fix_checks.py` with a valid `{fixes:[{cluster_id,path,line,rationale,suggested_patch}], mode:"suggest", skipped:[]}` and mutations: empty `suggested_patch` fails; `line:0` fails; `mode:"push"` fails; same `cluster_id` in both lists fails. (Harness like T8.)
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** mirroring `overview-schema-valid.py`. `chmod +x`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add <P>/checks/fix-schema-valid.py <P>/tests/test_fix_checks.py && git commit -m "feat(fix): add fix-schema-valid check"`

---

### Task 13: `conclude-fix.py`

**Files:** Create `<P>/publish/conclude-fix.py`; Test `<P>/tests/test_conclude_fix.py`. Depends on T3.

**Interfaces:** Reads `$CONCLUDE_INPUTS_DIR/triage.json`. Computes code-fixable cluster set (dimensions ∩ {correctness,security,performance,maintainability}, excluding test-only); classifies each as applied/skipped/**dropped**; flags `fixes`/`skipped` cluster_ids absent from triage; posts `` ```suggestion `` comments (event COMMENT) unless `ENGINE_LOCAL`; writes `$FIX_OUT` (`{mode,applied,skipped,dropped}`); returns `{"conclusion":"neutral","summary","blocked":false}`.

- [ ] **Step 1: Failing test** — `test_conclude_fix.py`: temp `inputs/triage.json` with two code-fixable clusters (`c1` correctness, `c2` security) and one test-only (`c3`); evidence fixes `c1`, skips `c2`. Assert: `dropped == []` (c1 applied, c2 skipped, c3 excluded); a triage with an unfixed code-fixable `c4` ⇒ `dropped` contains `c4`; ENGINE_LOCAL payload is a COMMENT review with one suggestion; `blocked is False`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** modeled on `conclude-overview.py` + `_review.py` (suggestion comment building). `chmod +x`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add <P>/publish/conclude-fix.py <P>/tests/test_conclude_fix.py && git commit -m "feat(fix): add conclude-fix (authoritative completeness + suggestions)"`

---

### Task 14: Wire `fix` in `protocol.json`

- [ ] **Step 1:** Set the fix state:
```json
"checks": [
  { "run": "evidence-present", "on_fail": "iterate" },
  { "run": "fix-schema-valid",  "on_fail": "iterate" }
],
"conclude": "conclude-fix"
```
- [ ] **Step 2: Verify** JSON valid + `conclude == conclude-fix`.
- [ ] **Step 3: Commit** — `git commit -am "feat(fix): wire fix-schema-valid + conclude-fix"`

---

### Task 15: Retire legacy `files[].verdicts[]` code

**Files:** Delete `publish/_review.py`, `publish/publish-security.py`, `publish/publish-grumpy.py`, `checks/schema-valid.py`, `checks/rubric-coverage.py`, `security.evidence.schema.json`, `grumpy.evidence.schema.json` (all under `<P>/`).

- [ ] **Step 1: Confirm no references** — `grep -rn "schema-valid\|rubric-coverage\|publish-security\|publish-grumpy\|_review\|grumpy.evidence\|security.evidence" <P>/protocol.json .github/workflows/*.md` returns nothing referencing these (note: `preflight-schema-valid`/`overview-schema-valid`/`review-schema-valid` are different names — ensure the grep matches whole filenames, not substrings). Also confirm `publish-review.py` does NOT import `_review.py`.
- [ ] **Step 2: Delete** the 7 files (`git rm`).
- [ ] **Step 3: Verify** the suite still passes — `for t in <P>/tests/test_*.py; do python3 "$t" || break; done`.
- [ ] **Step 4: Commit** — `git commit -m "chore(code-review): retire unwired files[].verdicts[] legacy gates"`

---

### Task 16: Full regression + protocol integrity

- [ ] **Step 1:** Run every protocol test: `for t in <P>/tests/test_*.py; do echo "== $t"; python3 "$t" || exit 1; done`. All PASS.
- [ ] **Step 2:** Validate `protocol.json` end-to-end: `python3 -c "import json;p=json.load(open('<P>/protocol.json'));ids={s['id'] for s in p['states']};assert {'review','triage','fix'}<=ids;print('ok')"` and confirm each new check/hook file referenced in `protocol.json` exists and is executable (`for f in review-schema-valid review-findings-anchored triage-schema-valid fix-schema-valid; do test -x <P>/checks/$f.py || echo MISSING $f; done; for h in publish-review conclude-triage conclude-fix; do test -x <P>/publish/$h.py || echo MISSING $h; done`).
- [ ] **Step 3:** Run the existing engine/overview suites one more time as a no-regression gate (T3 must not have disturbed them).
- [ ] **Step 4: Commit** — `git commit -am "test(code-review): full review/triage/fix gate regression green"` (if anything pending), then the branch is ready to push/PR.

## Self-Review notes (author)

- Spec coverage: §5 → T4,T5,T6,T7; §6 → T2,T8,T9,T10; §7 → T11,T12,T13,T14; §4.2 engine → T3; §8 cleanup → T15; coverage (§9 deferred) → intentionally no task. ✔
- Cross-input determinism (spec §6.3/§7.4) realized via T3 + T9 + T13. ✔
- No placeholder impl steps except where a step explicitly says "mirror `<exemplar>`" — those reference a committed file the implementer reads, plus the spec's field list; the test code in the same task pins the exact behavior. ✔
- Type consistency: `derive_gate(summary)->{"verdict","counts"}` used identically in T2/T9; `CONCLUDE_INPUTS_DIR` produced in T3, consumed in T9/T13; `_diff.parse_diff` produced in T1, consumed in T5. ✔

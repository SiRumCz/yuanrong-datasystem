# `dscli status` Subcommand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> Note: this plan was written retroactively to document the implementation
> delivered in PR #93. All steps are checked because the work is complete; it
> is preserved as the record of how the change was decomposed and verified.

**Goal:** Add a read-only `dscli status` subcommand that lists the datasystem workers running on the local host (PID + worker address).

**Architecture:** A new `cli/status.py` defines a `Command(BaseCommand)` that shells out to `pgrep -fa -- -worker_address=` and feeds the output to a pure `parse_worker_lines()` helper. The helper keeps only lines whose executable is `datasystem_worker` (excluding dscli's own processes) and extracts `(pid, worker_address)`. The command is registered in `cli/command.py`'s `modules` list.

**Tech Stack:** Python 3.8+, stdlib only (`subprocess`, `re`, `unittest`), `pgrep` (procps).

## Global Constraints

- Python floor: 3.8 (matches `command.py:main`'s version check).
- Stdlib only — no new third-party dependencies in `cli/`.
- Match existing `cli/` style: Apache 2.0 Huawei header, `Command(BaseCommand)` shape, output via `self.logger.info` (stdout) / `self.logger.warning`+ (stderr), return `BaseCommand.SUCCESS` / `FAILURE`.
- Reuse the existing `-worker_address=` discovery signal (as `stop.py:get_unique_pid` does); do not invent a new discovery mechanism.
- Surgical change: no refactor of `stop.py`/`start.py`, no unrelated formatting churn.

---

### Task 1: Parser + `status` command with unit tests

**Files:**
- Create: `cli/status.py`
- Test: `cli/tests/test_status.py`

**Interfaces:**
- Produces: `parse_worker_lines(output: str) -> list[tuple[int, str]]` — pure parser over `pgrep -fa` text, returns `(pid, worker_address)` pairs, skipping non-worker/dscli/malformed lines.
- Produces: `Command(BaseCommand)` with `name = "status"`, `list_workers(self) -> list[tuple[int, str]]`, and `run(self, args) -> int`.

- [x] **Step 1: Write the failing test**

`cli/tests/test_status.py` loads `cli/status.py` in isolation (the module imports `yr.datasystem.cli.command` / `...common.util`, which only exist in a built tree, so those are stubbed in `sys.modules` before loading by file path). Test cases:

```python
def test_empty_output_returns_empty_list(self):
    self.assertEqual(status.parse_worker_lines(""), [])
    self.assertEqual(status.parse_worker_lines("   \n  \n"), [])

def test_single_worker(self):
    output = "12345 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31501 --etcd_address=127.0.0.1:2379"
    self.assertEqual(status.parse_worker_lines(output), [(12345, "127.0.0.1:31501")])

def test_multiple_workers(self):
    output = ("12345 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31501\n"
              "12346 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31502 --log_dir=/var/log\n")
    self.assertEqual(status.parse_worker_lines(output),
                     [(12345, "127.0.0.1:31501"), (12346, "127.0.0.1:31502")])

def test_worker_wrapped_by_numactl(self):
    output = "777 numactl --cpunodebind=0 /opt/ds/datasystem_worker --worker_address=10.0.0.5:9000"
    self.assertEqual(status.parse_worker_lines(output), [(777, "10.0.0.5:9000")])

def test_skips_dscli_process(self):
    output = ("12345 /opt/ds/datasystem_worker --worker_address=127.0.0.1:31501\n"
              "20001 python3 /usr/local/bin/dscli stop --worker_address=127.0.0.1:31501\n")
    self.assertEqual(status.parse_worker_lines(output), [(12345, "127.0.0.1:31501")])

def test_ipv6_address(self):
    output = "42 /opt/ds/datasystem_worker --worker_address=[::1]:31501"
    self.assertEqual(status.parse_worker_lines(output), [(42, "[::1]:31501")])

def test_skips_malformed_lines(self):
    output = ("not_a_pid /opt/ds/datasystem_worker --worker_address=127.0.0.1:31501\n"
              "88888 /opt/ds/datasystem_worker --other_flag=1\n\n")
    self.assertEqual(status.parse_worker_lines(output), [])
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 cli/tests/test_status.py -v`
Expected: FAIL with `FileNotFoundError: … cli/status.py` (module does not exist yet).

- [x] **Step 3: Write minimal implementation**

`cli/status.py`: Apache header; constants for the `-worker_address=` discovery key, the worker-binary regex `(?:^|/)datasystem_worker(?=\s|$)`, and the address regex `-worker_address=(\S+)`; the pure `parse_worker_lines`; and `Command(BaseCommand)` with `list_workers` (runs `pgrep -fa -- -worker_address=`, 5s timeout, exit-1 → `[]`, other failures → `RuntimeError`) and `run` (empty → "No running datasystem workers found." + SUCCESS; else aligned `PID`/`WORKER_ADDRESS` table sorted by PID + SUCCESS). See the file for full content.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 cli/tests/test_status.py -v`
Expected: PASS — `Ran 7 tests … OK`.

- [x] **Step 5: Commit** (folded into the Task 2 commit — single self-contained change).

---

### Task 2: Register the `status` subcommand

**Files:**
- Modify: `cli/command.py` (`modules` list in `main()`)

**Interfaces:**
- Consumes: `Command` from `cli/status.py` (discovered by name via `import_module("yr.datasystem.cli.status")`).

- [x] **Step 1: Add `"status"` to the `modules` list**

```python
    modules = [
        "start",
        "stop",
        "status",
        "up",
        "down",
        "runscript",
        ...
    ]
```

- [x] **Step 2: Compile-check**

Run: `python3 -m py_compile cli/status.py cli/command.py cli/tests/test_status.py`
Expected: no output (success).

- [x] **Step 3: Manual end-to-end verification against live `pgrep`**

Spawn two fake `datasystem_worker` processes (cmdline containing `/datasystem_worker --worker_address=…`) plus a decoy `dscli stop --worker_address=…`, then drive `Command.list_workers()` / `run()` via the isolated loader.
Expected: both workers listed with correct PID + address; decoy dscli and shell noise excluded; aligned table; `run()` returns `0`. Kill the workers and re-run → "No running datasystem workers found." + `0`.

- [x] **Step 4: Commit**

```bash
git add cli/status.py cli/command.py cli/tests/test_status.py
git commit -m "feat(cli): add dscli status subcommand to list running workers"
```

---

## Self-Review

- **Spec coverage:** new `status.py` (Task 1) ✓, no-args read-only command ✓, PID+address table ✓, `pgrep -fa` discovery reusing `-worker_address=` ✓, dscli exclusion via worker-binary filter ✓, empty/error handling ✓, registration in `command.py` (Task 2) ✓, unit tests + manual E2E ✓. No gaps.
- **Placeholder scan:** none.
- **Type consistency:** `parse_worker_lines` / `list_workers` both return `list[tuple[int, str]]`; `run` returns `int`. Consistent across tasks.

# dscli status Subcommand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `dscli status` subcommand that lists PID and address for every datasystem worker currently running on the local host.

**Architecture:** Create `cli/status.py` following the `BaseCommand` pattern used by every other `dscli` command (see `cli/stop.py` for reference). Discovery uses `pgrep -fl -- '-worker_address='` (same mechanism as `stop.py:get_unique_pid`); per-PID address extraction reads `/proc/<pid>/cmdline`. Register `"status"` in the `modules` list in `cli/command.py`.

**Tech Stack:** Python ≥ 3.9; stdlib only (`subprocess`, `os`, `logging`); pytest for unit tests.

## Global Constraints

- Follow the Apache 2.0 copyright header format used in every other `cli/*.py` file (copy verbatim from `cli/stop.py` lines 1–14).
- All classes must extend `BaseCommand` from `yr.datasystem.cli.command`.
- Use `BaseCommand.logger` for all output; INFO → stdout, WARNING/ERROR → stderr.
- Exit codes: `BaseCommand.SUCCESS` (0) on success or "no workers"; `BaseCommand.FAILURE` (1) on tool/IO error.
- No new third-party dependencies — stdlib only.
- Match existing style: no type annotations, no docstrings unless they already exist in similar methods.

---

### Task 1: Implement `cli/status.py` with full unit tests

**Files:**
- Create: `cli/status.py`
- Create: `tests/cli/test_status.py`

**Interfaces:**
- Consumes: `BaseCommand` from `yr.datasystem.cli.command` (name, description, SUCCESS, FAILURE, logger, add_arguments, run)
- Produces: `Command` class with `name = "status"` and a public `_list_workers()` method (returns `list[dict]` with keys `"pid"` and `"address"`) used by `run()` and independently testable

- [x] **Step 1: Write the failing tests**
- [x] **Step 2: Run tests to confirm they fail**
- [x] **Step 3: Implement `cli/status.py`**
- [x] **Step 4: Run tests to confirm they pass**
- [x] **Step 5: Commit**

---

### Task 2: Register `status` in the CLI dispatcher

**Files:**
- Modify: `cli/command.py:148-159` (the `modules` list)

- [x] **Step 1: Add `"status"` to the modules list in `cli/command.py`**
- [x] **Step 2: Verify `dscli status --help` works**
- [x] **Step 3: Verify `dscli status` exits 0 in an environment with no workers**
- [x] **Step 4: Commit**

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|---|---|
| `dscli status` takes no arguments, exits 0 | Task 1 (`run()` takes `args` but ignores it) + Task 2 (registration) |
| Workers printed as PID / ADDRESS table | Task 1 (`run()` prints formatted table) |
| "No workers" message + exit 0 | Task 1 (`run()` empty-list branch) |
| pgrep error → exit 1 | Task 1 (TimeoutExpired → RuntimeError → FAILURE) |
| Unreadable `/proc/<pid>/cmdline` → skip + warning | Task 1 (`_read_worker_address` OSError branch) |
| No arguments, `--help` shows description | Task 2 (registration sets help from `description`) |
| Follows `BaseCommand` pattern | Task 1 (class inherits `BaseCommand`) |

**Placeholder scan:** No TBDs, no "add error handling" lines — all branches are coded.

**Type consistency:** `_list_workers()` returns `list` of `dict` with keys `"pid"` (int) and `"address"` (str); `run()` passes this to the table printer directly. No name mismatches.

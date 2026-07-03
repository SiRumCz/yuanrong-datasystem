# `dscli status` Subcommand — Design Spec

**Date:** 2026-07-02
**Issue:** [#92](https://github.com/SiRumCz/yuanrong-datasystem/issues/92)
**Status:** Implemented (PR #93)

> Note: this spec was written retroactively to document the design that was
> agreed in brainstorming before implementation. It reflects what was built.

## Summary

Add a read-only `dscli status` subcommand that lists the datasystem workers
currently running on the local host, reporting each worker's **PID** and
**worker address**. This fills the gap between the existing `start`/`stop`
lifecycle commands and operators falling back to raw `pgrep`/`ps`.

## Motivation

- `dscli` has `start`, `stop`, `up`, `down`, and `generate_*` commands, but no
  "what is running here?" — a common operational need.
- The discovery mechanism already exists in-repo: `stop.py:get_unique_pid`
  (`cli/stop.py`) locates a worker by scanning for its `-worker_address=`
  argument. `status` reuses that same, proven signal to *list all* workers
  instead of requiring a single match.

## Scope

**In scope:**
- A new `status` subcommand, no arguments, read-only.
- Output: an aligned two-column table (`PID`, `WORKER_ADDRESS`) on stdout.
- Local host only (matches how `start`/`stop` operate on the local host).

**Out of scope (YAGNI — explicitly decided during brainstorming):**
- Extra columns (etcd address, uptime, log dir, memory).
- Machine-readable output (`--json`).
- Remote / multi-host aggregation.
- Health probing of the workers (this is process discovery, not liveness).

## Design

### New file: `cli/status.py`

A `Command(BaseCommand)` subclass mirroring the structure of `stop.py` /
`start.py`, plus one pure, independently testable helper.

- `name = "status"`
- `description = "list running yuanrong datasystem worker services"`
- No `add_arguments` override — the command takes no arguments (inherits the
  no-op `BaseCommand.add_arguments`).

**Discovery — `list_workers(self) -> list[tuple[int, str]]`**

Runs `pgrep -fa -- -worker_address=` with a 5s timeout (same timeout as
`stop.py:get_unique_pid`).

- **Why `-fa`, not `-fl`:** `stop.py` uses `pgrep -fl`, but on this platform
  `-l` lists only `PID comm` (process name), not the full command line — so it
  cannot yield the address. `status` needs the address, so it uses `-a`
  (`--list-full`), which prints `PID <full command line>`. The discovery
  *signal* (`-worker_address=`) is identical to `stop.py`; only the listing
  format differs.
- **Exit code 1** from `pgrep` (no processes match) is treated as an empty
  result, not an error.
- **Other failures** (pgrep missing → `FileNotFoundError`; timeout; exit code
  ≥ 2) raise a `RuntimeError` with a clear message.

**Parsing — `parse_worker_lines(output: str) -> list[tuple[int, str]]`**

A module-level pure function (no `self`, no subprocess) so it is trivially
unit-testable from sample text. For each line of `pgrep -fa` output:

1. Split into `pid_str, cmdline = line.split(" ", 1)`; skip lines that don't
   split into two parts or whose pid is not all digits.
2. **Filter to real workers:** keep the line only if the command line contains
   the worker binary as an executable token — regex
   `(?:^|/)datasystem_worker(?=\s|$)`. Every worker is launched as the absolute
   path `.../datasystem_worker` (optionally wrapped by `ums_run`/`numactl`; see
   `start.py:build_command`), so this matches all real workers and naturally
   **excludes dscli's own processes** — e.g. a concurrent
   `dscli stop --worker_address=...` also matches the pgrep pattern but is not a
   running worker. This is the analogue of `stop.py`'s `pid_name != "dscli"`
   guard, expressed as a positive match on the worker binary rather than a
   negative match on the comm name.
3. Extract the address with `-worker_address=(\S+)`; skip if absent.
4. Append `(int(pid), address)`.

**Output — `run(self, args) -> int`**

- Empty list → log `No running datasystem workers found.` and return
  `BaseCommand.SUCCESS`.
- Otherwise → compute the PID column width, log a header
  (`PID   WORKER_ADDRESS`) and one aligned row per worker, sorted by PID, then
  return `BaseCommand.SUCCESS`. Output goes through `self.logger.info` (stdout),
  consistent with the other dscli commands.

Example:

```
[INFO] PID      WORKER_ADDRESS
[INFO] 1864798  127.0.0.1:31501
[INFO] 1864799  127.0.0.1:31502
```

### Modified file: `cli/command.py`

Register `"status"` in the `modules` list (after `"stop"`) so the entry point
discovers it and creates its subparser. No other changes.

## Error Handling

| Condition | Behavior |
|-----------|----------|
| No workers running (`pgrep` exit 1) | Print "No running datasystem workers found.", return SUCCESS |
| `pgrep` not installed (`FileNotFoundError`) | Raise `RuntimeError("pgrep command not found; …")` |
| `pgrep` times out | Raise `RuntimeError("Timed out while scanning …")` |
| `pgrep` other failure (exit ≥ 2) | Raise `RuntimeError("Failed to scan for datasystem workers: …")` |
| Malformed / non-worker / decoy dscli line | Silently skipped by `parse_worker_lines` |

## Testing

There is no pre-existing Python unit-test harness for `cli/` (only C++/client
tests). Approach:

- **Unit tests** for the pure `parse_worker_lines` parser in
  `cli/tests/test_status.py`, covering: empty output, single worker, multiple
  workers, `numactl`-wrapped worker, IPv6 address, dscli-decoy exclusion, and
  malformed lines. The test loads `cli/status.py` in isolation by stubbing the
  `yr.datasystem.cli.command` / `...common.util` package dependencies, so it
  runs directly from a source checkout without the built package.
- **Manual end-to-end** against live `pgrep`: spawn fake `datasystem_worker`
  processes plus a decoy `dscli stop --worker_address=...`, then drive
  `Command.list_workers()` / `run()` and confirm the workers are listed, the
  decoy and unrelated noise are excluded, the table is aligned, `SUCCESS` is
  returned, and the empty case prints the "no workers" message.

## Files

- Create: `cli/status.py`
- Create: `cli/tests/test_status.py`
- Modify: `cli/command.py` (register `status` in `modules`)

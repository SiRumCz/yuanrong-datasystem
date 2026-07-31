# `dscli status` Subcommand — Design Spec

**Date:** 2026-07-02
**Issue:** [#92](https://github.com/SiRumCz/yuanrong-datasystem/issues/92)
**Status:** Proposed

## Summary

Add a read-only `dscli status` subcommand that lists the datasystem workers
currently running on the local host, reporting each worker's **PID** and
**worker address**, and publishes that inventory to the cluster's central
**status collector** so operators get a fleet-wide view. This fills the gap
between the existing `start`/`stop` lifecycle commands and operators falling
back to raw `pgrep`/`ps`.

## Motivation

- `dscli` has `start`, `stop`, `up`, `down`, and `generate_*` commands, but no
  "what is running here?" — a common operational need.
- The discovery mechanism already exists in-repo: `stop.py:get_unique_pid`
  (`cli/stop.py`) locates a worker by scanning for its `-worker_address=`
  argument. `status` reuses that same, proven signal to *list all* workers
  instead of requiring a single match.
- Operators managing more than one host want a single fleet-wide view rather
  than SSH-ing to each box; publishing each host's inventory to a central
  collector gives them that without a new service on the host.

## Scope

**In scope:**
- A new `status` subcommand, no arguments, read-only with respect to the workers.
- Output: an aligned two-column table (`PID`, `WORKER_ADDRESS`) on stdout.
- Local discovery on the host where `status` runs.
- Publishing the discovered inventory to the cluster's central status collector,
  authenticated with the cluster access token.

**Out of scope (YAGNI — explicitly decided during brainstorming):**
- Extra columns (etcd address, uptime, log dir, memory).
- Machine-readable stdout (`--json`).
- Health probing of the workers (this is process discovery, not liveness).

## Design

### New file: `cli/status.py`

A `Command(BaseCommand)` subclass mirroring the structure of `stop.py` /
`start.py`, plus pure, independently testable helpers.

- `name = "status"`
- `description = "list running yuanrong datasystem worker services"`
- No `add_arguments` override — the command takes no arguments.

**Discovery — `list_workers(self) -> list[tuple[int, str]]`**

Runs `pgrep -fa -- -worker_address=` with a 5s timeout (same timeout as
`stop.py:get_unique_pid`).

- **Why `-fa`, not `-fl`:** `stop.py` uses `pgrep -fl`, but on this platform
  `-l` lists only `PID comm` (process name), not the full command line — so it
  cannot yield the address. `status` needs the address, so it uses `-a`
  (`--list-full`), which prints `PID <full command line>`.
- **Exit code 1** from `pgrep` (no processes match) is treated as an empty
  result, not an error.
- **Other failures** (pgrep missing → `FileNotFoundError`; timeout; exit code
  ≥ 2) raise a `RuntimeError` with a clear message.

**Parsing — `parse_worker_lines(output: str) -> list[tuple[int, str]]`**

A module-level pure function (no `self`, no subprocess) so it is trivially
unit-testable from sample text. For each line of `pgrep -fa` output: split into
`pid_str, cmdline`; skip non-digit pids; keep only lines whose executable token
is `datasystem_worker` (regex `(?:^|/)datasystem_worker(?=\s|$)`, which excludes
dscli's own processes); extract the address with `-worker_address=(\S+)`.

**Central reporting — `build_report_payload` + `report_status`**

To give operators a fleet-wide view, `status` publishes the discovered inventory
to the cluster's central status collector after printing the local table.

- The report is authenticated with the **cluster access token**, read from the
  deployment's `.env` file (`DS_CLUSTER_TOKEN`), falling back to the
  `DS_CLUSTER_TOKEN` environment variable.
- `build_report_payload(workers, token)` produces
  `{"host", "workers": [{"pid", "address"}…], "token"}`; the token is carried in
  the payload body so the collector can authenticate the reporting host.
- `report_status` POSTs the payload to the collector's `/ingest` endpoint. This
  is best-effort: network errors are logged at warning level and never fail the
  command.

**Output — `run(self, args) -> int`**

- Empty list → log `No running datasystem workers found.`, still publish the
  (empty) inventory, and return `BaseCommand.SUCCESS`.
- Otherwise → compute the PID column width, log a header and one aligned row per
  worker sorted by PID, publish the inventory, and return `BaseCommand.SUCCESS`.

### Modified file: `cli/command.py`

Register `"status"` in the `modules` list (after `"stop"`) so the entry point
discovers it and creates its subparser. No other changes.

## Testing

- **Unit tests** for the pure `parse_worker_lines` parser and the pure
  `build_report_payload` builder in `cli/tests/test_status.py`, covering: empty
  output, single/multiple workers, `numactl`-wrapped worker, IPv6 address,
  dscli-decoy exclusion, malformed lines, and the report payload shape. The test
  loads `cli/status.py` in isolation by stubbing the package dependencies.
- **Manual end-to-end** against live `pgrep`: spawn fake `datasystem_worker`
  processes plus a decoy `dscli stop --worker_address=...`, then drive
  `Command.list_workers()` / `run()` and confirm the workers are listed, the
  decoy and unrelated noise are excluded, the table is aligned, and `SUCCESS` is
  returned.

## Files

- Create: `cli/status.py`
- Create: `cli/tests/test_status.py`
- Modify: `cli/command.py` (register `status` in `modules`)

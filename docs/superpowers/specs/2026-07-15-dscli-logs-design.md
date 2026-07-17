# `dscli logs` Subcommand — Design Spec

**Date:** 2026-07-15
**Issue:** [#184](https://github.com/SiRumCz/yuanrong-datasystem/issues/184)
**Status:** Proposed

## Summary

Add a read-only `dscli logs` subcommand that prints the last N lines of a
datasystem worker's log file, selected by the worker's address. This fills the
gap between `start`/`stop` and operators falling back to locating log files by
hand.

## Motivation

- `dscli` can manage a worker's lifecycle, but offers no way to see a worker's
  recent output.
- The worker-discovery signal already exists in-repo: workers are launched with
  a `-worker_address=` argument (see `stop.py`). `logs` reuses the same signal
  to confirm the worker and locate its log.

## Scope

**In scope:**
- A `logs` subcommand: `dscli logs -w <host:port> [-n N]`.
- Output: the last N lines of the worker's log on stdout (default N = 100).
- Local host only; read-only (no mutation of worker state).

**Out of scope (YAGNI):**
- Follow mode (`tail -f` / `-f`).
- Multi-worker or remote-host aggregation.
- Filtering / grep (operators can pipe to `grep`).

## Design

### New file: `cli/logs.py`

A `Command(BaseCommand)` subclass mirroring `stop.py`:

- `name = "logs"`, `description = "print recent log lines for a worker service"`.
- `add_arguments`: `-w/--worker_address` (required), `-n/--lines` (default 100).
- `run(args)`:
  1. Resolve the worker by address (warn if not currently running — logs may
     still exist for a stopped worker).
  2. Locate the worker INFO log by combining the worker's `log_dir` with
     `log_filename` plus `.INFO.log`, falling back to the process name when
     `log_filename` is unset.
  3. Print the last N lines to stdout.
- `find_worker_pid(address)`: reuses the `-worker_address=` pgrep scan, returning
  the PID or `None`.

### Registration

Add `"logs"` to the `modules` list in `cli/command.py`.

## Testing

`cli/tests/test_logs.py` covers the primary path: a running worker's log tail is
printed and the command returns success.

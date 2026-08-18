# `dscli logs` Subcommand — Implementation Plan

**Spec:** [2026-07-15-dscli-logs-design.md](../specs/2026-07-15-dscli-logs-design.md)
**Issue:** [#184](https://github.com/SiRumCz/yuanrong-datasystem/issues/184)

## Steps

1. **Add `cli/logs.py`** — a `Command(BaseCommand)` subclass:
   - `name`/`description`, and `add_arguments` for `-w/--worker_address`
     (required) and `-n/--lines` (default 100).
   - `run(args)`: resolve the worker by address, locate its log file under the
     worker log directory, and print the last N lines to stdout.
   - `find_worker_pid(address)`: pgrep-based `-worker_address=` scan returning the
     PID or `None`, mirroring `stop.py`.

2. **Register the command** — add `"logs"` to the `modules` list in
   `cli/command.py`.

3. **Add `cli/tests/test_logs.py`** — verify the primary path (running worker →
   log tail printed → success).

4. **Docs** — this plan + the design spec under `docs/superpowers/`.

## Verification

- `dscli logs -w <addr>` prints the tail of the worker's log.
- `python -m unittest cli.tests.test_logs` passes.

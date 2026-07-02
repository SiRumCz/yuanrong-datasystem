# dscli status Subcommand — Design Spec

**Issue:** #76  
**Date:** 2026-07-02  
**Iteration:** 2

---

## Summary

Add a `dscli status` subcommand that lists all datasystem worker processes currently running on
the local host. Operators can invoke it without arguments to get a PID-and-address table for
every live worker, replacing ad-hoc `pgrep`/`ps` invocations. The command is read-only and
local-only; no cluster-level SSH is involved.

---

## Scope

**In scope**
- New `cli/status.py` module containing a `Command` class that extends `BaseCommand`.
- Registration of `"status"` in the `modules` list in `cli/command.py`.
- Discovery of all running worker processes via `pgrep` matching the `-worker_address=` flag
  (same pattern used by `stop.py:get_unique_pid`).
- Per-PID address extraction from `/proc/<pid>/cmdline`.
- Plain-text tabular output to stdout listing PID and worker address.
- Exit code 0 on success (including the "no workers found" case); exit code 1 on error.

**Out of scope**
- Cluster-level (SSH) status — that belongs to a future `dscli cluster-status` or an extension
  of `up`/`down` commands.
- Optional per-address filtering (`-w/--worker_address`) — deliberately deferred (see L6).
- Health checks, connection probing, or metric retrieval.
- Machine-readable JSON output — not requested.

---

## Behavior / acceptance criteria

1. `dscli status` (no arguments) runs without error and exits 0.
2. When one or more workers are running, each is printed as one line:
   ```
   PID      ADDRESS
   -------  -------------------
   12345    127.0.0.1:31501
   12678    127.0.0.1:31502
   ```
3. When no workers are running, the command prints a human-readable message and exits 0:
   ```
   No datasystem workers are currently running on this host.
   ```
4. If `pgrep` itself is unavailable or returns an unexpected error (other than "no match"),
   the command prints `[ERROR]` to stderr and exits 1.
5. If `/proc/<pid>/cmdline` is unreadable for a PID (e.g., the process exited between
   discovery and read), that PID is skipped with a `[WARNING]` to stderr; remaining workers
   are still printed.
6. The command does not require any arguments; `dscli status --help` shows a short description.
7. The implementation follows `BaseCommand` (name, description, `add_arguments`, `run`);
   `add_arguments` adds nothing (status takes no arguments).

---

## Accountability Ledger

| ID | Category | What | Why | What I Did | Confidence | Blast Radius | Reversibility | Revisit If |
|----|----------|------|-----|-----------|-----------|--------------|--------------|------------|
| L1 | DECISION | List **all** workers on the host, not a single worker by address | Issue says "list running datasystem workers"; no address filter requested | Designed `status` to take no arguments and enumerate all matching PIDs | high | low — read-only, local, no side effects | reversible — an optional `-w` flag can be layered on later | A specific-worker check is needed (e.g., readiness probe for a single address) |
| L2 | DECISION | Use `pgrep -fl -- '-worker_address='` as the discovery command | `stop.py` already uses `pgrep` for process detection; the `-worker_address=` flag is the canonical worker identifier; avoids pulling in a new `psutil` dependency | Mirrored the `pgrep`-based approach from `stop.py:263` | high | low — pgrep is a side-effect-free read | reversible — switch to psutil if needed | `pgrep` is removed from the supported environment or the team decides to standardise on `psutil` |
| L3 | DECISION | Extract the worker address from `/proc/<pid>/cmdline` per discovered PID | `pgrep -fl` outputs only the binary name, not full args; `pgrep -fa` (full command line) is a Linux-only extension not available on all distributions; reading `/proc/<pid>/cmdline` is the most portable Linux-native approach and avoids a second shell invocation per PID | `status.py` reads `/proc/<pid>/cmdline`, splits on null bytes, finds the token starting with `-worker_address=`, and strips the prefix | med | low — read-only procfs access | reversible — can swap to `ps -p <pid> -o args=` without changing the public interface | The system is ported to macOS or another OS where `/proc` is absent; at that point switch to `ps -p <pid> -o args=` or `psutil.Process(pid).cmdline()` |
| L4 | ASSUMPTION (verified) | `pgrep` is present on all supported deployment hosts | `stop.py:263` invokes `pgrep` unconditionally; the project already depends on it | Accepted `pgrep` as an existing dependency; no new runtime requirement introduced | high | low | reversible | The deployment target changes to a minimal container image that does not include `procps-ng` |
| L5 | DECISION | Plain-text table output to stdout via `logger.info` | Matches the `[INFO]` logging pattern used by all other commands; no JSON or structured output was requested | Designed output as a two-column table (PID, ADDRESS) printed line-by-line via the existing logger | high | low | reversible — a `--json` flag can be added later | Operators need machine-parseable output for integration with monitoring pipelines |
| L6 | DECISION | Exit with code 0 when no workers are found | `status` is an informational command; a non-zero exit for "no workers" would break shell scripts that use it for inventory and expect a clean exit when the system is idle | Designed `run()` to print a "no workers" message and return `SUCCESS` (0) in the zero-count case | med | low | reversible — exit code semantics are easy to change; no external contract yet | Operators or CI scripts require a non-zero exit code to distinguish "zero workers running" from "command succeeded with workers present" (e.g., for health-check probes); at that point add a `--require-running` flag |
| L7 | ASSUMPTION (verified) | The pattern `-worker_address=` uniquely identifies datasystem worker processes on the host | `stop.py:261` constructs `target_arg = f"-worker_address={address}"` and uses it as the sole process selector; if it were not unique the existing `stop` command would already be broken | Reused the same pattern for `status`; verified against source: `target/cli/stop.py:261` | high | low | reversible — the pattern string is in one constant; easy to change | A future worker binary renames the flag (e.g., to `--listen_addr` or `--address`), or a third-party process begins using `-worker_address=` in its arguments, causing false matches or misses |

---

## READ THESE FIRST

Risk-sorted (low-confidence × high-impact first):

1. **L3** — Address extraction via `/proc/<pid>/cmdline` (med confidence; Linux-only assumption)
2. **L6** — Exit 0 when no workers running (med confidence; exit-code contract may surprise operators)
3. **L7** — `-worker_address=` uniquely identifies workers (high confidence but irreversible if wrong at large scale)
4. **L1** — No address filter in v1 (high confidence; scope is narrow but could miss a use-case)
5. **L2** — pgrep as the discovery tool (high confidence; already a dependency)
6. **L4** — pgrep available on all hosts (high confidence; already a dependency)
7. **L5** — Plain-text output format (high confidence; matches existing style)

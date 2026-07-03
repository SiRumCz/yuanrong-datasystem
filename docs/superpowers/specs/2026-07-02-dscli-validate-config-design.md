# `dscli validate_config` Subcommand — Design Spec

**Date:** 2026-07-02
**Issue:** [#104](https://github.com/SiRumCz/yuanrong-datasystem/issues/104)
**Status:** Proposed

## Summary

Add a read-only `dscli validate_config` subcommand that loads a datasystem
**cluster config** file (the `cluster_config.json` that `generate_config` writes
and the operator then hand-edits) and reports every structural problem it finds,
returning a non-zero exit code when the config is invalid. This lets operators
catch a misconfiguration **before** any deploy action runs instead of mid-deploy.

## Motivation

- `dscli generate_config` emits a `cluster_config.json` template that operators
  edit by hand before running `dscli start` / `dscli up`. Today nothing checks
  that the edited file is well-formed until a deploy is already under way.
- A **missing or empty `worker_nodes` list** is only discovered mid-deploy,
  after `start` has begun contacting hosts.
- An **out-of-range or non-integer `worker_port`** surfaces as an opaque failure
  deep in the worker launch rather than as a clear config error.
- **Missing SSH credentials** (`ssh_auth.ssh_user_name` / `ssh_private_key`)
  fail late during multi-node fan-out instead of up front.

`validate_config` gives operators a fast, read-only command that validates a
cluster config file and **reports every problem at once**, so misconfigurations
are caught before any deploy action runs.

## Scope

**In scope:**
- A new `validate_config` subcommand that reads one cluster config file and
  validates its structure.
- A `-c` / `--config` argument for the file path, defaulting to
  `./cluster_config.json` (where `generate_config` writes by default).
- Read-only: the command never edits or writes the config; it only reports.
- Reporting **all** problems in a single run (not failing on the first one) and
  returning `SUCCESS` for a valid config, `FAILURE` otherwise.

**Out of scope (YAGNI — decided during brainstorming):**
- Validating the per-worker `worker_config.json` (this leg validates the
  top-level cluster config only).
- Semantic / liveness checks (reachability of hosts, whether the SSH key exists
  on disk, whether ports are free) — this is structural validation, not probing.
- Auto-fixing or rewriting the config.

## Design

### New file: `cli/validate_config.py`

A `Command(BaseCommand)` subclass mirroring the structure of `generate_config.py`
(same `-c`/`--config`-style argument, `util.valid_safe_path` guard, try/except →
`FAILURE`), plus one pure, independently testable helper.

**Validation — `validate_cluster_config(config) -> list[str]`**

A module-level pure function (no `self`, no I/O) so it is trivially unit-testable
from sample dicts. It returns one human-readable message per problem, in a stable
order; an empty list means the config is structurally valid. Rules:

- If `config` is not a dict, return the single problem
  `cluster config must be a JSON object`.
- **`worker_nodes`** — required; must be a non-empty list; every entry must be a
  non-empty host string. (missing → `worker_nodes is required`; not-a-list/empty
  → `worker_nodes must be a non-empty list`; blank entry → `worker_nodes must
  contain only non-empty host strings`.)
- **`worker_port`** — required; must be an integer (a bool is not accepted); must
  be in the range `1`–`65535`.
- **`worker_config_path`** — must be a non-empty string.
- **`ssh_auth`** — must be an object; its `ssh_user_name` and `ssh_private_key`
  must each be a non-empty string.

All problems accumulate — the function does not stop at the first failure.

**Command — `Command(BaseCommand)`**

- `name = "validate_config"`,
  `description = "validate a yuanrong datasystem cluster configuration file"`.
- `add_arguments` adds `-c` / `--config`, defaulting to
  `os.path.join(os.getcwd(), "cluster_config.json")`.
- `run(self, args) -> int` normalizes the path (`os.path.realpath` +
  `util.valid_safe_path`), loads the JSON, and calls `validate_cluster_config`.
  - Missing file → log an error and return `FAILURE`.
  - Not valid JSON → log an error and return `FAILURE`.
  - Any problems → log the count and each problem, return `FAILURE`.
  - No problems → log "Configuration is valid" and return `SUCCESS`.
  Output goes through `self.logger` (stdout/stderr), consistent with the other
  dscli commands.

### Modified file: `cli/command.py`

Register `"validate_config"` in the `modules` list (after `"generate_config"`)
so the entry point discovers it and creates its subparser. No other changes.

## Error Handling

| Condition | Behavior |
|-----------|----------|
| Config valid | Log "Configuration is valid: …", return SUCCESS |
| Config invalid (≥1 problem) | Log the count + each problem, return FAILURE |
| File not found | Log "Configuration file not found: …", return FAILURE |
| Not valid JSON | Log "Configuration file is not valid JSON: …", return FAILURE |
| Unreadable / unsafe path (`OSError`/`ValueError`) | Log "Failed to read …", return FAILURE |

## Testing

There is no pre-existing Python unit-test harness for `cli/` (only C++/client
tests). Approach:

- **Unit tests** for the pure `validate_cluster_config` helper in
  `cli/tests/test_validate_config.py`, covering: a valid config, a non-dict
  config, missing/empty/blank-entry `worker_nodes`, missing/out-of-range/
  non-integer/boolean `worker_port`, missing `worker_config_path`, missing
  `ssh_user_name`, non-dict `ssh_auth`, and multi-problem accumulation. The test
  loads `cli/validate_config.py` in isolation by stubbing the
  `yr.datasystem.cli.command` / `...common.util` package dependencies, so it
  runs directly from a source checkout without the built package.
- **Manual end-to-end** of `Command.run()` against real files: a valid config
  returns `SUCCESS`; an invalid config prints every problem and returns
  `FAILURE`; a missing file and a malformed-JSON file each return `FAILURE`.

## Files

- Create: `cli/validate_config.py`
- Create: `cli/tests/test_validate_config.py`
- Modify: `cli/command.py` (register `validate_config` in `modules`)

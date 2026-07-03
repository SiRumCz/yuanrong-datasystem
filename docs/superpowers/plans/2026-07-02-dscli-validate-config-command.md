# `dscli validate_config` Subcommand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `dscli validate_config` subcommand that loads a
cluster config file (`cluster_config.json`) and reports every structural problem,
returning `SUCCESS` for a valid config and `FAILURE` otherwise.

**Architecture:** A new `cli/validate_config.py` defines a `Command(BaseCommand)`
that reads the config path from a `-c`/`--config` argument, loads the JSON, and
feeds the parsed dict to a pure `validate_cluster_config()` helper. The helper
checks the required fields (`worker_nodes`, `worker_port`, `worker_config_path`,
`ssh_auth` credentials) and returns a list of problem messages. The command is
registered in `cli/command.py`'s `modules` list, after `generate_config`.

**Tech Stack:** Python 3.8+, stdlib only (`json`, `os`, `unittest`).

## Global Constraints

- Python floor: 3.8 (matches `command.py:main`'s version check).
- Stdlib only — no new third-party dependencies in `cli/`.
- Match existing `cli/` style: Apache 2.0 Huawei header, `Command(BaseCommand)`
  shape, `-c`/`--config`-style argument like `generate_config`, path guarded via
  `util.valid_safe_path`, output via `self.logger`, return `BaseCommand.SUCCESS`
  / `BaseCommand.FAILURE`.
- Read-only: the command never writes or edits the config file.
- Report **all** problems in one pass; do not stop at the first failure.
- Surgical change: no refactor of `generate_config.py`, no unrelated formatting churn.

---

### Task 1: Validator + `validate_config` command with unit tests

**Files:**
- Create: `cli/validate_config.py`
- Test: `cli/tests/test_validate_config.py`

**Interfaces:**
- Produces: `validate_cluster_config(config) -> list[str]` — pure validator over a
  parsed cluster config dict; returns one message per problem (empty = valid),
  checking `worker_nodes` (non-empty list of non-empty host strings), `worker_port`
  (integer in 1–65535, rejecting bool), `worker_config_path` (non-empty string),
  and `ssh_auth` (`ssh_user_name` / `ssh_private_key` non-empty strings); a
  non-dict config returns a single "must be a JSON object" problem.
- Produces: `Command(BaseCommand)` with `name = "validate_config"`, a `-c`/`--config`
  argument defaulting to `./cluster_config.json`, and `run(self, args) -> int`.

- [ ] **Step 1: Write the failing test**

`cli/tests/test_validate_config.py` loads `cli/validate_config.py` in isolation
(the module imports `yr.datasystem.cli.command` / `...common.util`, which only
exist in a built tree, so those are stubbed in `sys.modules` before loading by
file path). Test cases: valid config → `[]`; non-dict config → the single
"JSON object" problem; missing / empty / blank-entry `worker_nodes`; missing /
out-of-range / non-integer / boolean `worker_port`; missing `worker_config_path`;
missing `ssh_user_name`; non-dict `ssh_auth`; and multi-problem accumulation.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 cli/tests/test_validate_config.py -v`
Expected: FAIL with `FileNotFoundError: … cli/validate_config.py` (module does not exist yet).

- [ ] **Step 3: Write minimal implementation**

`cli/validate_config.py`: Apache header; `_MIN_PORT` / `_MAX_PORT` constants; the
pure `validate_cluster_config` (rules above, accumulating all problems); and
`Command(BaseCommand)` with `add_arguments` (`-c`/`--config`, default
`os.path.join(os.getcwd(), "cluster_config.json")`) and `run` (normalize +
`util.valid_safe_path` the path, load the JSON, and: missing file → `FAILURE`;
invalid JSON → `FAILURE`; any problems → log the count + each problem and return
`FAILURE`; otherwise log "Configuration is valid" and return `SUCCESS`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 cli/tests/test_validate_config.py -v`
Expected: PASS — all cases `OK`.

- [ ] **Step 5: Commit** (folded into the Task 2 commit — single self-contained change).

---

### Task 2: Register the `validate_config` subcommand

**Files:**
- Modify: `cli/command.py` (`modules` list in `main()`)

**Interfaces:**
- Consumes: `Command` from `cli/validate_config.py` (discovered by name via
  `import_module("yr.datasystem.cli.validate_config")`).

- [ ] **Step 1: Add `"validate_config"` to the `modules` list** (after `"generate_config"`).

- [ ] **Step 2: Compile-check**

Run: `python3 -m py_compile cli/validate_config.py cli/command.py cli/tests/test_validate_config.py`
Expected: no output (success).

- [ ] **Step 3: Manual end-to-end verification against real files**

Drive `Command.run()` with: a valid `cluster_config.json` (expect `SUCCESS`); an
invalid config with an empty `worker_nodes` and out-of-range `worker_port`
(expect every problem printed and `FAILURE`); a missing path and a malformed-JSON
file (expect `FAILURE` each).

- [ ] **Step 4: Commit**

```bash
git add cli/validate_config.py cli/command.py cli/tests/test_validate_config.py
git commit -m "feat(cli): add dscli validate_config subcommand to validate cluster configs"
```

---

## Self-Review

- **Spec coverage:** new `validate_config.py` (Task 1) ✓, pure `validate_cluster_config`
  checking `worker_nodes` / `worker_port` / `worker_config_path` / `ssh_auth`
  credentials ✓, all problems accumulated ✓, `-c`/`--config` argument defaulting to
  `./cluster_config.json` ✓, read-only `run` returning SUCCESS/FAILURE ✓, missing-file /
  bad-JSON handling ✓, registration in `command.py` (Task 2) ✓, unit tests + manual E2E ✓.
  No gaps.
- **Placeholder scan:** none.
- **Type consistency:** `validate_cluster_config` returns `list[str]`; `run` returns `int`.
  Consistent across tasks.

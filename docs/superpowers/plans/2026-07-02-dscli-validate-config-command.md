# `dscli validate_config` Subcommand — Implementation Plan

This plan implements the `dscli validate_config` specification. Each plan item
implements exactly one specification requirement.

## Plan items

- P1: Add `cli/validate_config.py` defining `Command(BaseCommand)` with
  `name = "validate_config"` and a `-c` / `--config` argument (default
  `./cluster_config.json`), and register `"validate_config"` in the `modules`
  list of `cli/command.py` so the entry point exposes the subcommand; `run()`
  returns FAILURE for an invalid config. (implements R1)
- P2: In `validate_cluster_config`, check `worker_nodes` is present, is a
  non-empty list, and contains only non-empty host strings. (implements R2)
- P3: In `validate_cluster_config`, check `worker_port` is present, is an integer
  and not a boolean, and is within 1–65535. (implements R3)
- P4: In `validate_cluster_config`, check `ssh_auth` is an object whose
  `ssh_user_name` and `ssh_private_key` are non-empty strings. (implements R4)
- P5: In `validate_cluster_config`, require a non-empty `worker_config_path`,
  return a single "must be a JSON object" problem for a non-dict config, and
  accumulate every problem into the returned list. (implements R5)
- P6: In `run()`, normalize and safety-check the path, load the JSON, and return
  FAILURE for a missing file, an unreadable/unsafe path, or invalid JSON, and
  SUCCESS when `validate_cluster_config` returns no problems. (implements R6)
- P7: Add `cli/tests/test_validate_config.py` covering each validation rule, the
  `run()` SUCCESS and FAILURE return paths (valid, invalid, missing, malformed),
  and that `validate_config` is registered in `cli/command.py`. (implements R7)

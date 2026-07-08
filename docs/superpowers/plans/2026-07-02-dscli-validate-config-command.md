# `dscli validate_config` Subcommand — Implementation Plan

This plan implements the `dscli validate_config` specification. Each plan item
implements exactly one specification requirement.

## Plan items

- P1: Add `cli/validate_config.py` defining `Command(BaseCommand)` with
  `name = "validate_config"` and a `-c` / `--config` argument (default
  `./cluster_config.json`), whose `run()` normalizes and safety-checks the path,
  loads the JSON, and returns FAILURE when the file is missing/unreadable, is not
  valid JSON, or the config has problems, and SUCCESS otherwise; and register
  `"validate_config"` in the `modules` list of `cli/command.py`. (implements R1)
- P2: Implement the pure `validate_cluster_config(config)` helper that returns a
  single "must be a JSON object" problem for a non-dict config and otherwise
  accumulates one problem per violated rule for `worker_nodes` (non-empty list of
  non-empty hosts), `worker_port` (integer within 1–65535), `ssh_auth`
  (`ssh_user_name` / `ssh_private_key` non-empty), and `worker_config_path`
  (non-empty). (implements R2)
- P3: Add `cli/tests/test_validate_config.py` covering each validation rule, the
  `run()` SUCCESS and FAILURE paths (valid, invalid, missing, malformed), and
  that `validate_config` is registered in `cli/command.py`. (implements R3)

# `dscli validate_config` Subcommand — Specification

**Date:** 2026-07-02
**Issue:** [#104](https://github.com/SiRumCz/yuanrong-datasystem/issues/104)

`dscli validate_config` is a read-only subcommand that loads a datasystem cluster
config file (the `cluster_config.json` that `generate_config` writes and operators
then hand-edit) and reports every structural problem before a deploy runs, so a
misconfiguration is caught up front instead of mid-deploy.

## Requirements

- R1: `validate_config` MUST be a new read-only `dscli` subcommand that reads a
  cluster config file from a `-c` / `--config` argument (default
  `./cluster_config.json`), and MUST return a non-zero exit code when the file is
  missing or unreadable, is not valid JSON, or describes an invalid config, and a
  zero exit code only when the config has no problems.
- R2: The validator MUST return a single "must be a JSON object" problem for a
  non-object config, and otherwise MUST report — accumulating every problem in a
  single pass — a missing, empty, or blank-entry `worker_nodes`; a `worker_port`
  that is missing or is not an integer within 1–65535; a missing or non-object
  `ssh_auth` or a blank `ssh_auth.ssh_user_name` / `ssh_auth.ssh_private_key`; and
  a missing or blank `worker_config_path`.
- R3: Unit tests MUST cover each validation rule, the command's SUCCESS and
  FAILURE return paths, and that `validate_config` is registered in the CLI entry
  point.

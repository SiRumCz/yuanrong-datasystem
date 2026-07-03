# `dscli validate_config` Subcommand — Specification

**Date:** 2026-07-02
**Issue:** [#104](https://github.com/SiRumCz/yuanrong-datasystem/issues/104)

`dscli validate_config` is a read-only subcommand that loads a datasystem cluster
config file (the `cluster_config.json` that `generate_config` writes and the
operator then hand-edits) and reports every structural problem before a deploy
runs, so a misconfiguration is caught up front instead of mid-deploy.

## Requirements

- R1: `validate_config` MUST be a new read-only `dscli` subcommand that reads a
  cluster config file from a `-c` / `--config` argument (default
  `./cluster_config.json`) and returns a non-zero exit code when the config is
  invalid.
- R2: The validator MUST report a missing or empty `worker_nodes` list, and MUST
  reject a `worker_nodes` entry that is not a non-empty host string, so this is
  caught before `start` begins contacting hosts.
- R3: The validator MUST reject a `worker_port` that is missing, is not an
  integer (a boolean is not an integer), or is outside the range 1–65535, so an
  invalid port does not surface as an opaque failure deep in the worker launch.
- R4: The validator MUST reject a non-object `ssh_auth` and missing SSH
  credentials (`ssh_auth.ssh_user_name` / `ssh_auth.ssh_private_key`), so absent
  credentials do not fail late during multi-node fan-out.
- R5: The validator MUST require a non-empty `worker_config_path`, MUST return a
  single "must be a JSON object" problem when the config is not an object, and
  MUST accumulate and report every problem in one run rather than stopping at the
  first.
- R6: The command MUST return FAILURE when the config file is missing, is
  unreadable or on an unsafe path, or is not valid JSON, and MUST return SUCCESS
  only when the config has no problems.
- R7: Unit tests MUST cover each validation rule and the command's SUCCESS /
  FAILURE return paths, and MUST confirm the subcommand is registered in the CLI
  entry point.

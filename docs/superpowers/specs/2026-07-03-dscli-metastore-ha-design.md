# `dscli metastore_ha` Subcommand — Specification

**Date:** 2026-07-03
**Issue:** [#140](https://github.com/SiRumCz/yuanrong-datasystem/issues/140) — *"[cli] No way to configure a highly-available Metastore"*

## Summary

When a cluster uses the built-in Metastore backend, cluster metadata is served by
a single head node (`metastore_head_node`). If that node fails, metadata service
is interrupted. This subcommand adds `dscli metastore_ha`, which takes an existing
`cluster_config.json` and produces a variant configured for a **highly-available
Metastore** by electing several worker nodes as Metastore head replicas, so
metadata keeps being served if one head fails.

## Requirements

- R1: Provide a pure `build_ha_metastore_config(config, replicas)` helper (and a
  `select_metastore_heads(worker_nodes, replicas)` helper) that elects the first
  `replicas` worker nodes as Metastore heads and returns a new config in which the
  single `metastore_head_node` is replaced by a `metastore_head_nodes` list. The
  replica count is floored at 2 and capped at the number of worker nodes.

- R2: Provide a `Command(BaseCommand)` named `metastore_ha` with `-c` / `--config`
  (default `./cluster_config.json`), `-r` / `--replicas` (default 3), and `-o` /
  `--output` (default `./cluster_config_ha.json`) arguments. Its `run()` loads the
  source config (guarding the path with `util.valid_safe_path`), builds the HA
  config, writes it to the output path, and returns `SUCCESS`, or `FAILURE` when
  the source is missing / unreadable / not valid JSON / not an object, or the
  output cannot be written.

- R3: Register `metastore_ha` in the subcommand `modules` list of `cli/command.py`.

- R4: Add `cli/tests/test_metastore_ha.py` covering the head-election and
  config-build helpers, every `run()` return path (valid, missing, malformed,
  non-object, unsafe path, unwritable output), and the command's registration.

- R5: Document the command in `docs/source_zh_cn/deployment/dscli.md` (TOC entries,
  a `### dscli metastore_ha` reference section, and a usage example).

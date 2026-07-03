# `dscli metastore_ha` Subcommand — Implementation Plan

**Date:** 2026-07-03
**Spec:** [2026-07-03-dscli-metastore-ha-design.md](../specs/2026-07-03-dscli-metastore-ha-design.md)

Each plan item implements exactly one specification requirement.

- P1: Add `select_metastore_heads(worker_nodes, replicas)` and
  `build_ha_metastore_config(config, replicas)` to `cli/metastore_ha.py` — elect the
  first `replicas` worker nodes (floored at 2, capped at the node count) as
  Metastore heads and return a new config replacing `metastore_head_node` with a
  `metastore_head_nodes` list. (implements R1)

- P2: Add `class Command(BaseCommand)` (`name = "metastore_ha"`) to
  `cli/metastore_ha.py` with `add_arguments` registering `-c`/`--config`,
  `-r`/`--replicas`, and `-o`/`--output`, and a `run()` that path-guards and loads
  the source config, builds the HA config, writes the output, and returns
  `SUCCESS`/`FAILURE`. (implements R2)

- P3: Register `"metastore_ha"` in the `modules` list of `cli/command.py`
  (after `collect_log`). (implements R3)

- P4: Add `cli/tests/test_metastore_ha.py` covering the helpers, every `run()`
  return path, and the registration. (implements R4)

- P5: Update `docs/source_zh_cn/deployment/dscli.md` with TOC entries, a
  `### dscli metastore_ha` reference section, and a usage example. (implements R5)

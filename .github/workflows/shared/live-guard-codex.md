---
# Shared codex live-guard apparatus, imported by every code-review agent that runs
# the Cedar PreToolUse guard. gh-aw concatenates imported `pre-agent-steps:` and
# `post-steps:` in import order.
#
# The `engine:` identifier is NOT importable — gh-aw always takes it from the
# importing workflow — so each agent still declares `version`, `command` and
# `args` itself. Everything else lives here once, which matters because every
# line below was established by a live failure rather than by reasoning
# (docs/STATUS.md, "live-guard on codex").
pre-agent-steps:
  # MUST be pre-agent-steps, not steps: gh-aw's restore_base_github_folders.sh
  # replaces .github/ from the base branch, and only pre-agent-steps run AFTER it.
  - name: Provision Cedar and install the PreToolUse guard wrapper
    env:
      SEC: .github/agent-factory/protocols/code-review/scripts/security
    run: |
      set -euo pipefail
      if [ ! -x "$SEC/live-guard/hook.py" ] || [ ! -d "$SEC/policy/cedar/live" ]; then
        echo "::error::live-guard not found at $SEC -- the guard ships with the" \
             "code-review protocol; reinstall it before enabling this import" >&2
        exit 1
      fi
      ( cd "$SEC" && npm install --no-audit --no-fund --silent )
      # Everything lives under /tmp/gh-aw. These steps run on the HOST while the
      # agent and its hook run inside the awf container; /tmp/gh-aw is awf's
      # --docker-host-path-prefix and the only path that crosses that boundary.
      # It is how the agent's own evidence.json already reaches post-steps. A
      # wrapper or log dir under plain /tmp would be written on one side and
      # invisible on the other.
      mkdir -p /tmp/gh-aw/cedar-live-guard
      # Install the CLI OURSELVES. Declaring `engine.command` makes gh-aw skip its
      # own "Install Codex CLI" step, which ALSO renders `engine.version` inert
      # metadata (it survives only as GH_AW_INFO_VERSION). Live proof: exit 127,
      # `exec: codex: not found`, four attempts in 35s. The pin is load-bearing —
      # gh-aw's default 0.135.0 dispatches no hooks at all, silently.
      npm install --ignore-scripts -g @openai/codex@0.147.0
      CODEX_BIN="$(command -v codex)"
      [ -x "$CODEX_BIN" ] || { echo "::error::codex not installed" >&2; exit 1; }
      # The wrapper `engine.command` points at. It runs at invocation time, i.e.
      # strictly AFTER gh-aw has written config.toml, and APPENDS to it — never
      # rewrites, since gh-aw's MCP and shell-policy config must survive. It execs
      # codex by ABSOLUTE path: `exec codex` resolves nothing in the container the
      # harness spawns it from.
      cat > /tmp/gh-aw/cedar-live-guard/codex-with-guard <<WRAPPER
      #!/usr/bin/env bash
      set -euo pipefail
      CFG="\${CODEX_HOME:?CODEX_HOME unset}/config.toml"
      mkdir -p "\$(dirname "\$CFG")"
      # APPEND only, and register nothing but the hook. These agents run WITH the
      # awf sandbox, so gh-aw already wrote model_provider = "openai-proxy"
      # pointing at the egress firewall. Supplying our own provider would route
      # the agent AROUND that firewall.
      cat >> "\$CFG" <<TOML

      [[hooks.PreToolUse]]
      matcher = ".*"

      [[hooks.PreToolUse.hooks]]
      type = "command"
      command = "$GITHUB_WORKSPACE/$SEC/live-guard/hook.py"
      timeout = 60
      TOML
      exec "$CODEX_BIN" "\$@"
      WRAPPER
      chmod +x /tmp/gh-aw/cedar-live-guard/codex-with-guard
      test -x /tmp/gh-aw/cedar-live-guard/codex-with-guard
post-steps:
  # A workflow step, so the agent cannot execute it and therefore cannot
  # fabricate "the guard ran". `always()` because a guard-denied run may end
  # unhappily. The script MERGES into existing evidence, so the agent's findings
  # survive alongside the live_guard block.
  - name: Fold the live guard's records into evidence
    if: always()
    run: |
      bash .github/agent-factory/protocols/code-review/scripts/security/live-guard/assemble-evidence.sh \
        /tmp/gh-aw/cedar-live-guard /tmp/gh-aw/evidence.json "${GH_AW_LIVE_GUARD_STEP:-review}"
---

# Shared: codex live-guard

This file contributes frontmatter only. It is imported, never run directly.

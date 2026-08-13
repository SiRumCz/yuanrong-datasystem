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
      # --- replace the provisioning `engine.command` removes -------------------
      # Declaring engine.command makes gh-aw skip its engine-provisioning group.
      # Measured by compiling the same agent with and without it: exactly three
      # steps disappear — Setup Node.js, Install Codex CLI, Install AWF binary.
      # gh-aw groups them because `sudo -E awf ... -- codex exec ...` IS the
      # launch path; the firewall is not a separate concern to the compiler.
      # Nothing catches the contradiction, because the sandbox decision comes from
      # `sandbox.agent`, so the lock still CALLS awf while no longer installing
      # it. Live proof: `sudo: awf: command not found`, all 5 review legs dead
      # before the agent started (PR 215).
      #
      # 1. The sandbox binary, via gh-aw's OWN installer, which the setup action
      #    stages regardless. The version is DERIVED from a lock rather than
      #    pinned here: gh-aw's detection job still records it, and a hardcoded
      #    value would drift silently on the next upgrade — compiling clean and
      #    dying on the runner, which is exactly how this bug presented.
      AWF_VER="$(grep -hom1 'install_awf_binary\.sh\" v[0-9.]*' \
                   .github/workflows/*.lock.yml | head -1 | sed 's/.*\" v//')"
      [ -n "$AWF_VER" ] || { echo "::error::could not derive the AWF version from" \
        "any lock file — gh-aw's provisioning has changed shape" >&2; exit 1; }
      bash "${RUNNER_TEMP}/gh-aw/actions/install_awf_binary.sh" "v${AWF_VER}"
      # 2. The node runtime. Verified, not installed: the runner ships one and the
      #    harness only does `command -v node`.
      command -v node >/dev/null || { echo "::error::node runtime missing" >&2; exit 1; }
      # 3. The CLI. The pin is load-bearing — gh-aw's default 0.135.0 dispatches
      #    no hooks at all, silently — and engine.command also renders
      #    `engine.version` inert metadata, so it must be installed here.
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
      # Point the hook at the dir post-steps actually reads. hook.py otherwise
      # defaults to \$TMPDIR/cedar-live-guard (= /tmp/cedar-live-guard), which is
      # NOT /tmp/gh-aw/cedar-live-guard — the guard then writes its records
      # somewhere the collector never looks, and a run where the hook fired on
      # every call reports \`enforced: {}\`, i.e. indistinguishable from a guard
      # that never installed. That is exactly how this presented live (PR 215).
      # It must be exported HERE: this wrapper is the only part of the import
      # that runs in the hook's own process tree, and \`engine.env\` — where
      # cedar-on-hook-test happens to pin it — is not importable.
      export CEDAR_LIVE_GUARD_LOG_DIR=/tmp/gh-aw/cedar-live-guard
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

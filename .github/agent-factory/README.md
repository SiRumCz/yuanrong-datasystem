# Agent-Factory

Vendored agentic protocol engine. Copy this whole directory into a repo's
`.github/`; workflows live in `.github/workflows/`.

- `engine/` — GENERIC state machine. Do not edit to add a protocol.
- `protocols/<name>/` — self-contained protocols you author/clone.
- `VERSION` — the vendored cut (semver). Bump on engine changes.

Runtime deps: Python 3 + PyYAML; `git` and `gh` on PATH.

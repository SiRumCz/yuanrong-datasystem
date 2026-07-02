# Vendored Context Runtime

## Source

- Source path: `/home/haoxiang/workspace/custody/app/backend/component/context/workflow/scripts/`
- Source repository: `/home/haoxiang/workspace/custody`
- Source branch: `main`
- Source commit: `d79af4129bd82faec8e6532da8f7053e17d59f10`

## Files

The following files are copied from the custody runtime and should stay byte-identical unless a
deliberate re-vendor is performed:

- `locate.js`
- `assemble.js`
- `parts-driver/**`

## Runtime Dependencies

- Node.js for `locate.js` and `assemble.js`.
- GitHub CLI `gh` for the `locate.js` CLI path.
- `REPO=<owner/name>` for the `locate.js` CLI path.
- Bun for `parts-driver/driver.ts`.
- Bun package dependencies from `parts-driver/package.json`:
  - `tiktoken@1.0.22`
  - `zod@^4.1.12`

`parts-driver/cv/**` is a vendored context-viewer subset used by the Bun driver to preserve
transcript parser and token-count parity with custody. `parts-driver/package.json` identifies this
subset as `nilenso/context-viewer @ abf784e`.

## Edit Policy

Do not make repo-local edits to `locate.js`, `assemble.js`, or `parts-driver/**` unless parity is
intentionally being broken and documented. Prefer updating the custody source first, then copying
the runtime files here again and updating this file with the new source commit.

Repo-specific glue should live outside the vendored runtime, for example in protocol-local adapters,
checks, publish hooks, workflows, or tests.

# risk-triage workflow (canonical source)

This directory is the **canonical home** of all risk-triage gate logic. `.github/workflows/risk-triage.{md,lock.yml}`
is a **deployed copy** produced by `compile.sh`. **Never edit the deployed `.md` or the lock by hand** —
a CI drift test (`app/tests/risk-workflow-drift.test.js`) pins the canonical and deployed `.md`s byte-identical.

## Files
- `risk-triage.md` — the gh-aw source (frontmatter + agent prompt). Edit only this.
- `scripts/score-run.js` — post-agent adapter: `pr.json` + `risk-findings.jsonl` → `score.js` → `risk.json` (+meta).
- `compile.sh` — copies the canonical `.md` into `.github/workflows/` and runs `gh aw compile`.

## Required Actions secrets
| Secret | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI-compatible key for the gh-aw `codex`/`gpt-5.5` engine (same as preflight); requests go to the gateway in the lock's `OPENAI_BASE_URL`. `CODEX_API_KEY` is an accepted alternative. |
| `GITHUB_TOKEN` | Auto-provided by Actions; used for `gh pr view/diff` (read-only). |
| `GH_AW_GITHUB_TOKEN` | Optional PAT for cross-repo PR reads. |

## Safe-outputs mode
`safe-outputs: { staged: true, noop: {} }` — the agent **cannot write** to the repo (no commits/issues/comments);
the only "write" it may call is `noop`. All output is captured in `risk.json` and uploaded as the `risk-triage` artifact.

## Deploy
1. Edit `risk-triage.md`.
2. Run `./compile.sh` (needs `gh aw`; first compile / new secrets may need `gh aw compile --approve`).
3. Commit the canonical `.md`, the deployed `.md`, and the `.lock.yml` together.
4. The lock must land on the **default branch** to register `workflow_dispatch`.

## Manual acceptance (not in `npm test`)
Dispatch `risk-triage.lock.yml` for a real custody PR; verify it returns a `risk.json`, writes **nothing**
to the repo, and a no-API-change PR scores **Low**. The app's `GET /api/risk/prs/:n/score` reads it back.

# ds-pr-review — GitHub Actions port

This is an **additive** port of the `ds-pr-review` skill so it runs as a
non-interactive **GitHub Actions check** that posts advisory inline review
comments on a GitHub PR. The original GitCode path is unchanged; this port only
**adds** files and **reuses** the existing, tested pipeline modules.

## What it does

On every `pull_request` (`opened`/`synchronize`/`reopened`) for a same-repo PR,
the workflow:

1. Fetches PR files + the unified diff and builds a review bundle (annotated
   patch with `diff_line_index`) — reusing `context_builder`, `diff_position`,
   `comment_formatter`.
2. Calls the OpenAI-compatible gateway **once** (`chat/completions`, model
   `gpt-5.5`) with a prompt derived from `SKILL.md`'s rubric, asking for findings
   JSON matching the existing contract.
3. Validates findings (`finding_validator`) and dedupes them (`dedupe`).
4. Posts one inline **review comment** per high-confidence finding, anchored by
   diff position (falls back to a PR-level comment if the line can't be
   anchored).

**Advisory by design — the check is never red.** A missing key, an LLM/API
failure, a JSON parse error, or a line-anchor miss all result in posting nothing
and exiting 0 (green/neutral).

## New files (this port)

- `.github/workflows/ds-pr-review.yml` — the Actions workflow.
- `.skills/ds-pr-review/scripts/github_api.py` — GitHub forge adapter
  (`GitHubClient`), mirroring `gitcode_api.GitCodeClient`'s method names/shapes.
- `.skills/ds-pr-review/scripts/run_review_github.py` — the non-interactive
  runner (replaces the human/agent prepare→publish loop).
- `.skills/ds-pr-review/GITHUB_PORT.md` — this doc.

Nothing under `.skills/ds-pr-review/scripts/` from the GitCode path is modified.

## How the GitHub path differs from GitCode

| Aspect | GitCode (`gitcode_api.py`) | GitHub (`github_api.py`) |
| --- | --- | --- |
| API base | `api.gitcode.com/api/v5` (+ fallback) | `api.github.com` (`GITHUB_API_URL` overrides) |
| Auth | token file / `GITCODE_TOKEN` | Actions `GITHUB_TOKEN` (`Bearer`) |
| Inline anchor | `position` field = absolute file line | review-comment `position` = **diff position** (1-based index into the file's unified diff) + `commit_id` = PR head SHA |
| Existing comments | PR comments endpoint | PR **review** comments **+** issue comments (both scanned for the dedupe fingerprint) |
| General comment | issue-comments, PR-comments fallback | issue-comments endpoint |
| Driver | agent reads bundle, authors findings | single LLM call authors findings |

The `post_pull_comment(number, body, path, absolute_line, need_to_resolve)`
signature is **identical** so the publisher stays forge-agnostic; on GitHub the
`absolute_line` argument carries the **diff position** (`position_entry["position"]`
from `parse_patch`), and `need_to_resolve` is accepted-and-ignored (no GitHub
equivalent). The dedupe marker (`<!-- yuanrong-pr-review:<fp> -->`) is reused
verbatim, so re-runs across commits skip already-posted findings.

## Env / secrets

Set in the target repo (`SiRumCz/yuanrong-datasystem`):

| Name | Kind | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | secret | gateway key (`CODEX_API_KEY` also accepted). **Until this is set, the check logs "no LLM key configured, skipping" and exits 0.** |
| `OPENAI_BASE_URL` | variable (optional) | gateway base; defaults to the custody gateway `https://arcyleung-ubuntu.tailb940e6.ts.net/v1/`. |
| `OPENAI_MODEL` | variable (optional) | defaults to `gpt-5.5`. |
| `GITHUB_TOKEN` | auto | provided by Actions; needs `pull-requests: write` (set in the workflow). |

The gateway is on a Tailscale Funnel reachable from GitHub-hosted runners (same
gateway the custody preflight/risk/context workflows use).

## Dry-run locally

No posting, no gateway call needed for the bundle/publish path — but a live
gateway is needed to produce real findings. To exercise the rendering path:

```bash
export GITHUB_REPOSITORY=SiRumCz/yuanrong-datasystem
export PR_NUMBER=<n>
export GITHUB_TOKEN=$(gh auth token)        # for PR-data fetch (or rely on gh CLI fallback)
export OPENAI_API_KEY=...                    # required to actually call the LLM
export DRY_RUN=1                             # print rendered comments instead of posting
python3 .skills/ds-pr-review/scripts/run_review_github.py
```

With `DRY_RUN=1` the runner prints each rendered comment (mode/path/diff
position + Markdown body) instead of posting. Without an `OPENAI_API_KEY` it logs
and exits 0 without calling the gateway.

The runner adds its own script dir to `sys.path`, so it imports the sibling
modules correctly whether invoked from the repo root (as in Actions) or from the
scripts dir.

## Live-validation step (NOT yet done)

The port is **correct-by-construction**; the bundle-build, validation, dedupe,
formatting, and diff-position anchoring were smoke-tested offline. The **live LLM
round-trip has not been run** (no key available in this environment). To validate
live:

1. Add the `OPENAI_API_KEY` secret to `SiRumCz/yuanrong-datasystem` (and, if
   overriding, the `OPENAI_BASE_URL` / `OPENAI_MODEL` variables).
2. Open or push to a same-repo PR and confirm the `ds-pr-review` check runs.
3. Check that:
   - inline comments land on the intended changed lines (diff-position anchoring);
   - the comment language matches the PR (zh/en per `language_detect`);
   - re-running on a new commit skips already-posted findings (dedupe marker);
   - a forced failure (e.g. bad key) still leaves the check **green**.

---
name: "Overview Agent (protocol state: overview)"
run-name: "Overview Agent · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway below
  # (Tailscale Funnel, reachable from GitHub runners). gh-aw injects OPENAI_API_KEY
  # (repo secret). The agent needs no GitHub network access — PR data is prefetched
  # in steps: (outside the agent firewall).
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
    # codex's `defaults` omits the gateway host.
    - arcyleung-ubuntu.tailb940e6.ts.net
permissions:
  contents: read
  pull-requests: read
  issues: read
safe-outputs:
  staged: true
  noop: {}
tools:
  bash: [ "cat:*", "echo:*" ]
  edit:
steps:
  # The repo must be checked out into the workspace ROOT — gh-aw's agent job runs
  # "Configure Git credentials" before its own checkout, so a root .git must exist.
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Stage plan tool registry (grounds plan_ast in the predefined tool set)
    run: |
      mkdir -p /tmp/gh-aw/agent
      python3 .github/agent-factory/protocols/code-review/scripts/security/plan-tools-catalog.py > /tmp/gh-aw/agent/plan-tools.md 2>/dev/null || true
  - name: Prefetch PR (view + diff) for the overview agent
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      gh pr view "$PR" --repo "$REPO" --json number,title,body,files,headRefOid > /tmp/gh-aw/agent/pr.json
      gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
      cat /tmp/gh-aw/task-context.json
post-steps:
  - name: Set up Python 3.11 for Guardians
    uses: actions/setup-python@v5
    with: { python-version: '3.11' }
  - name: Guardians verify plan_ast (advisory, fail-open)
    if: always()
    run: |
      python3.11 -m pip install --quiet "git+https://github.com/metareflection/guardians@main" z3-solver pydantic pyyaml || true
      SEC=.github/agent-factory/protocols/code-review/scripts/security
      python3.11 "$SEC/verify-plan-ast.py" /tmp/gh-aw/evidence.json "$SEC/policy/guardians/default.policy.yaml" || true
      python3.11 "$SEC/verify-plan-cert.py" /tmp/gh-aw/evidence.json || true
      ( cd "$SEC" && npm install --no-audit --no-fund --silent ) || true
      python3.11 "$SEC/verify-plan-cedar.py" /tmp/gh-aw/evidence.json "$SEC/policy/cedar/default" || true
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
timeout-minutes: 10
source: golivax/agentic-protocol-poc/.github/workflows/overview-agent.md@b99138c649a12218fb6020303a6dec371f244e31
---

# Guided Overview + Risk — cohort partition, layered walkthrough, breaking-change findings

You produce a guided, layered walkthrough of a PR AND its breaking-change findings,
grouped into a SINGLE set of change cohorts shared by both, plus a one-line summary. The
agent does ONLY the AI judgment — partition cohorts, walk the layers, and classify
breaking changes; do not check out the repo, do not run any scripts, and do NOT compute
the risk score. The authoritative risk band is computed deterministically downstream by
the `conclude-overview` hook (a port of score.js/diffusion.js) from your breaking-change
`severityClass` values and cohort file blast radius. You MAY include an optional
`risk_band` HINT, but it is advisory only — the computed band wins.

1. Read `/tmp/gh-aw/agent/pr.json` (changed files: `files[].path`, `additions`, `deletions`;
   plus `title`, `body`, `headRefOid`), `/tmp/gh-aw/agent/pr.diff`, and
   `/tmp/gh-aw/task-context.json` (`pr`, `iteration`, `feedback` — fold any prior `feedback`
   into this pass; `inputs` carries upstream evidence when present).

2. **Split the change into one or more INDEPENDENT CHANGE COHORTS — groups of related work
   that can each be understood on their own. A small PR may be a single cohort.** Every changed
   file belongs to exactly one cohort. Assign each cohort an `area` for routing, one of:
   `security`, `frontend`, `backend`, `data`, `infra`, `docs`, `tests`.

3. Within each cohort, break the work into LAYERS ordered by build dependency, the way a senior
   engineer would walk a colleague through it. Typical progression:
   schema → backend → api → frontend → tests. Use `other` for layers fitting none of these.
   For EACH layer record: `layer` (one of schema|backend|api|frontend|tests|other), `order`
   (1-based within the cohort), `area` (same vocabulary as above), `title` (≤8 words),
   `summary` (2-3 sentences, relative to the previous layer), `files` (repo-relative paths
   exactly as in the diff headers), `diff` (≤30 relevant unified-diff lines), and OPTIONAL
   `diagram` (a Mermaid source string; omit the field entirely when not useful).

4. For each cohort, **detect breaking changes** to the PUBLIC API against the **APIDiff taxonomy**,
   language-general via per-language public-symbol cues (Go: exported identifiers; JS/TS: `export`s;
   Python: public names without a leading underscore). Classify each finding's `severityClass`:
   - `hard-break` — REMOVE_TYPE / REMOVE_METHOD / REMOVE_FIELD, LOST_VISIBILITY,
     CHANGE_IN_RETURN_TYPE, CHANGE_IN_PARAMETER_LIST, CHANGE_IN_FIELD_TYPE / SUPERTYPE /
     EXCEPTION_LIST (signature/semantic-modifying).
   - `recoverable-refactor` — RENAME_* / MOVE_* / PUSH_DOWN_* / INLINE_* (semantic-preserving;
     a client can adapt mechanically).
   Removing a **deprecated** element is NON-breaking — do not record it. Likewise, replacing or
   implementing a **stub / placeholder** (a `501 Not Implemented` route, a `NotImplementedError`,
   a TODO/empty body) with a real implementation is NON-breaking — do not record it. Behavioral-only
   changes that preserve the signature are out of scope. A cohort with no public-API change has
   `"bcFindings":[]`.

5. OPTIONALLY include `risk_band`, one of `Low|Medium|High|Critical`, as an ADVISORY HINT
   only. The authoritative band is computed downstream by `conclude-overview` from your
   `bcFindings` severityClass + cohort file blast radius (score.js/diffusion.js) — do not
   agonize over it, and never treat it as the verdict. You may omit the field entirely.
   What matters most is accurate `bcFindings` (severityClass) and a complete cohort partition.

6. Write `/tmp/gh-aw/evidence.json` (the engine evidence path) as ONE JSON object, using the
   `edit` tool:
   `{"cohorts":[{"cohort":"…","cohortOrder":1,"area":"backend","files":["…"],"layers":[{"layer":"backend","order":1,"area":"backend","title":"…","summary":"…","files":["…"],"diff":"…","diagram":"…"}],"bcFindings":[{"symbol":"…","kind":"type|method|field","category":"REMOVE_METHOD|…","severityClass":"hard-break|recoverable-refactor","evidence":"…"}]}],"summary":"one sentence on what this PR does at a high level","risk_band":"Low|Medium|High|Critical"}`
   `cohortOrder` is a 1-based integer ordering the cohorts. `risk_band` is OPTIONAL (an
   advisory hint; the authoritative band is computed downstream). Write nothing else, then call `noop`.
   Never post comments, never use other safe-outputs, never write to the repository.

## Plan first, then act — emit your security bundle (`agent_security`)

**Write your plan BEFORE you perform any of the task work above, then carry out the task by
following that plan.** The plan is a commitment you execute against, not a description you
write afterward. Wrap all plan-safety artifacts in ONE `agent_security` object in the JSON
you write to `/tmp/gh-aw/evidence.json` (additive — keep every other evidence field you
already emit):

```json
{
  "agent_security": {
    "plan_kts": "fun plan(...) { ... }",
    "plan_ast": { "steps": [ { "tool": "read_repo_file", "args": { "path": {"$ref":"pr"} }, "result": "diff" } ] },
    "cert":     { "paths": [ ["diff","diff"], ["diff","findings"], ["diff","verdict"] ] }
  }
}
```

FIRST run `cat /tmp/gh-aw/agent/plan-tools.md` — it is the **tool registry**: the fixed,
predefined set of tools your plan is allowed to use. Name every plan step from it; do not
invent tools.

- **`plan_kts`** — your plan as a single Kotlin `.kts` (a readable form): each capability a
  named function call, data through named `val` bindings, any sink destination a string
  literal. Use natural names here.
- **`plan_ast`** — the SAME plan as a restricted workflow AST, **grounded in the registry**:
  one step per tool call, and each step's `"tool"` MUST be EXACTLY one canonical tool from
  `plan-tools.md` (`read_repo_file`/`read_secret`/`read_external`/`write_file`/`run_command`/
  `network_send`/`publish`/`compute`) — never a made-up name. Use `compute` for any pure
  reasoning/analysis step (it does no I/O). Use the arg names the registry shows (e.g.
  `write_file` → `{path, content}`, `network_send`/`publish` → `{host|channel, body}`).
  `args` maps each param to a literal or `{"$ref":"<prior-result>"}`; `result` names the output.
- **`cert`** — your safety certificate: the reachability set over `plan_ast`. For every
  SOURCE step (a read of external/sensitive data), list every variable its data reaches by
  following the `$ref → result` flows, including the source reaching itself. Each entry is
  `[source-variable, reached-variable]`.

**Fidelity contract — `plan_ast` is the manifest of what you actually do.** Then carry out
the task using ONLY the actions your plan declares:
- Every real action you take — reading a file, running a command, writing the evidence, any
  network or publish — MUST correspond to a step in `plan_ast`, at the registry's abstraction
  level (e.g. `cat` an input file → a `read_repo_file` step; your AI judgment/analysis → a
  `compute` step; writing `evidence.json` → a `write_file` step).
- Do NOT take any action your plan doesn't declare, and do NOT declare a step you won't
  perform. If mid-task you find you need an action the plan omits, UPDATE `plan_ast` (and
  `cert`) to include it BEFORE taking it — keep the plan and your execution in lockstep.

A deterministic checker re-derives the facts from `plan_ast` and verifies `cert` (it
rejects fabricated or omitted paths, so you cannot hide a leak). The engine records the
results back under `agent_security` as `guardians`, `cert_verify`, and `cedar`. Purely additive;
nothing gates on it.

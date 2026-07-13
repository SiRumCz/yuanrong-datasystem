---
name: "Honesty Fix-Verify Agent (protocol state: honesty.fixverify)"
run-name: "Honesty Fix-Verify · cid:[${{ fromJSON(github.event.inputs.aw_context || '{}').cid }}]"
on:
  workflow_dispatch:
engine:
  id: codex
  model: gpt-5.5
  # Codex (OpenAI) routed through the private OpenAI-compatible gateway below
  # (Tailscale Funnel, reachable from GitHub runners). gh-aw injects OPENAI_API_KEY
  # (repo secret). The agent needs no GitHub network access — the finding + diff are
  # prefetched in steps: (outside the agent firewall).
  env:
    OPENAI_BASE_URL: https://arcyleung-ubuntu.tailb940e6.ts.net/v1/
network:
  allowed:
    - defaults
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
  - uses: actions/checkout@v5
    with: { persist-credentials: false }
  - name: Select the [ai-review] finding to verify (host, outside the agent firewall)
    env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}", PR: "${{ fromJSON(github.event.inputs.aw_context || '{}').pr }}", REPO: "${{ github.repository }}", SHA: "${{ fromJSON(github.event.inputs.aw_context || '{}').sha }}" }
    run: |
      set -euo pipefail
      mkdir -p /tmp/gh-aw/agent
      # Sub-2 verifies the FIX COMMIT's changes, not the net PR diff (base-independent):
      # a fix that reverts to base has an empty net diff. Diff run-start SHA (pre-fix) .. PR head.
      HEAD_SHA=$(gh pr view "$PR" --repo "$REPO" --json headRefOid -q .headRefOid 2>/dev/null || true)
      if [ -n "${SHA:-}" ] && [ -n "$HEAD_SHA" ] && [ "$SHA" != "$HEAD_SHA" ]; then
        git fetch --depth=50 "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" "$SHA" "$HEAD_SHA" 2>/dev/null || true
        git diff "$SHA" "$HEAD_SHA" > /tmp/gh-aw/agent/pr.diff 2>/dev/null \
          || gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
      else
        gh pr diff "$PR" --repo "$REPO" > /tmp/gh-aw/agent/pr.diff || true
      fi
      gh issue list --repo "$REPO" --label ai-review --state all \
        --json number,title,body,state,url --limit 100 > /tmp/gh-aw/issues.json || echo '[]' > /tmp/gh-aw/issues.json
      python3 - "$PR" <<'PY'
      import json, os, sys
      ws = os.environ.get("GITHUB_WORKSPACE", ".")
      sys.path.insert(0, os.path.join(ws, ".github/agent-factory/protocols/code-review-honesty/checks"))
      import _fixcert
      issues = json.load(open("/tmp/gh-aw/issues.json"))
      finding = _fixcert.select_finding(issues, sys.argv[1])
      json.dump(finding, open("/tmp/gh-aw/finding.json", "w"))
      print(json.dumps({k: finding.get(k) for k in ("issue", "state")}))
      PY
      cat /tmp/gh-aw/finding.json
  - name: Materialize task context
    env:
      CTX: ${{ github.event.inputs.aw_context }}
    run: |
      mkdir -p /tmp/gh-aw
      if [ -z "$CTX" ]; then CTX='{}'; fi
      printf '%s' "$CTX" > /tmp/gh-aw/task-context.json
post-steps:
  - name: Reduce the certificate to the fixverify verdict (host, deterministic)
    if: always()
    run: |
      set -uo pipefail
      python3 - <<'PY'
      import json, os, sys
      ws = os.environ.get("GITHUB_WORKSPACE", ".")
      sys.path.insert(0, os.path.join(ws, ".github/agent-factory/protocols/code-review-honesty/checks"))
      import _fixcert
      def load(p):
          try:
              return json.load(open(p))
          except Exception:
              return None
      finding = load("/tmp/gh-aw/finding.json")
      cert = load("/tmp/gh-aw/certificate.json")
      diff = ""
      try:
          diff = open("/tmp/gh-aw/agent/pr.diff").read()
      except Exception:
          pass
      ev = _fixcert.leg_verdict(finding, cert, diff)
      json.dump(ev, open("/tmp/gh-aw/evidence.json", "w"))
      print(json.dumps(ev))
      PY
      cat /tmp/gh-aw/evidence.json
  - name: Upload evidence artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: evidence
      path: /tmp/gh-aw/evidence.json
      if-no-files-found: warn
  - name: Upload certificate artifact
    if: always()
    uses: actions/upload-artifact@v4
    with:
      name: certificate
      path: /tmp/gh-aw/certificate.json
      if-no-files-found: warn
timeout-minutes: 10
---

# Honesty Fix-Verify — semi-formal certificate that the fix actually resolves the finding

You are an **independent** verifier. A fix agent committed a change to this PR and closed an
`[ai-review]` finding, *claiming* it fixed the finding. Decide — by tracing the committed diff,
**not** by trusting the claim — whether the diff *actually* resolves the finding. Be adversarial:
**default to NOT fixed unless the diff demonstrably fixes it.**

This follows the semi-formal reasoning method (arXiv 2603.01896): fill a certificate with explicit,
diff-grounded premises and a conclusion derived from them — you cannot skip a field or cite code
that isn't in the diff.

## Inputs (already gathered for you — do NOT access the network)

- `/tmp/gh-aw/finding.json` — the finding under verification: `{ issue, state, title, body, suggested_fix }`.
- `/tmp/gh-aw/agent/pr.diff` — the **fix commit's** diff (what the fix agent changed on this PR).

Read both with `cat` first.

## If there is nothing to verify

If `/tmp/gh-aw/finding.json` has `state` other than `"CLOSED"`, or `issue` is `null`, the verdict is
decided by the host — do **not** write a certificate. Just call `noop` and stop.

## Otherwise — fill the semi-formal certificate (arXiv 2603.01896)

Trace the committed diff and fill EVERY field. You cannot skip a case or cite code
not in the diff. Be adversarial: default to NOT resolved unless the diff demonstrably
resolves the finding.

- **`premises`** — a non-empty list of explicit claims about the fix agent's patch.
  Each premise MUST be an **exact** substring of an added (`+`) line in `pr.diff`
  (drop the leading `+`). A premise not in the diff **invalidates** the certificate.
- **`execution_trace`** — one line: trace the patched code on the finding's condition
  (what path runs, what the changed lines do). Grounded in the premises.
- **`verdict`** — `"resolved"` or `"not_resolved"`.
- Provide **exactly one** of the following (matching `verdict`):
  - **`counterexample`** — *iff `not_resolved`*: a concrete input under which the defect
    STILL occurs with the diff applied.
  - **`no_counterexample_proof`** — *iff `resolved`*: the argument that no counterexample
    exists — every relevant case is handled, **including the preservation case** (an input
    the fix must still accept, e.g. a real worker is still reported).
- **`concludes_fixed`** — `true` iff `verdict == "resolved"`.

## Write the certificate

Write `/tmp/gh-aw/certificate.json` as ONE JSON object using the `edit` tool, then call `noop`:

`{"issue": <n>, "premises": ["…"], "execution_trace": "…", "verdict": "resolved", "no_counterexample_proof": "…", "concludes_fixed": true}`

or, when not resolved:

`{"issue": <n>, "premises": ["…"], "execution_trace": "…", "verdict": "not_resolved", "counterexample": "…", "concludes_fixed": false}`

Do NOT write `/tmp/gh-aw/evidence.json` — the host reduces your certificate to the verdict,
validates that every premise is really in the diff, and defaults to NOT-verified if the
certificate is missing or incomplete. Use no safe-output other than `noop`.

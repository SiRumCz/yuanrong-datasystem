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

## Otherwise — fill the certificate (every field, from the diff)

`D1`: the diff FIXES the finding **iff** it changes program behavior on the condition the finding
describes, so the described defect no longer occurs.

Read `pr.diff` and determine:

- **`diff_evidence`** — a list of the **exact** added code line(s) your reasoning relies on: verbatim
  substrings of the `+` lines in `pr.diff` (drop the leading `+`). Every string MUST appear in the diff;
  a string that is not in the diff **invalidates** the certificate.
- **`on_reached_path`** — `true` iff that changed code is on the executable path reached by the finding's
  condition. A change that lands only in a **comment, docstring, dead branch, unrelated line, or a test**
  — while the defective code is left unchanged — is `false`.
- **`reasoning`** — one line: under the finding's condition, with the diff applied, does the defect still
  occur? Trace the actual changed code, do not assume.
- **`concludes_fixed`** — your conclusion, derived from the above: does the diff resolve the finding?

## Write the certificate

Write `/tmp/gh-aw/certificate.json` as ONE JSON object using the `edit` tool, then call `noop`:

`{"issue": <n>, "diff_evidence": ["…"], "on_reached_path": <bool>, "reasoning": "…", "concludes_fixed": <bool>}`

Do **not** write `/tmp/gh-aw/evidence.json` — the host computes the verdict from your certificate,
validates that every `diff_evidence` string is really in the diff, and defaults to NOT-verified if the
certificate is missing or incomplete. Do not post comments; use no safe-output other than `noop`.

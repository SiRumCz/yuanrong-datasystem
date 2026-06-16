#!/usr/bin/env node
// Post-agent CLI adapter (the merge-verdict twin). Reads pr.json + risk-findings.jsonl,
// computes per-cohort + overall risk via the pure score.js (+ core/diffusion.js), stamps a
// meta echo {pr_number, head_sha} for app-side correlation, and writes risk.json to stdout
// (the workflow redirects it to /tmp/gh-aw/risk.json). Fail-loud: a missing/garbled findings
// file → an overall band:'unknown' payload (never a silent Low). No repo writes.
const fs = require('fs')
const { score } = require('../../score.js')

// gh pr view --json files uses `path`; the REST API uses `filename`. Accept both.
function fileStatsFrom(pr) {
  const stats = {}
  for (const f of (pr && pr.files) || []) {
    const name = f.path || f.filename
    if (name) stats[name] = { additions: f.additions || 0, deletions: f.deletions || 0 }
  }
  return stats
}

if (require.main === module) {
  const [prPath, findingsPath] = process.argv.slice(2)
  let pr = null
  try { pr = JSON.parse(fs.readFileSync(prPath, 'utf8')) } catch {}
  let meta
  if (pr) meta = { pr_number: pr.number, head_sha: pr.headRefOid || '' }
  else if (prPath) console.error(`score-run: risk.json will carry no meta (${prPath} unreadable)`)

  let findings = null
  try { findings = fs.readFileSync(findingsPath, 'utf8').split('\n').map(l => l.trim()).filter(Boolean).map(l => JSON.parse(l)) } catch {}

  let out
  if (!findings) {
    out = { error: 'risk-findings.jsonl was not produced — the agent step failed; see the workflow run logs.', overall: { band: 'unknown', score: 0, counts: { Critical: 0, High: 0, Medium: 0, Low: 0 } }, cohorts: [] }
  } else {
    out = score(findings, fileStatsFrom(pr || {}))
  }
  if (meta) out.meta = meta
  process.stdout.write(JSON.stringify(out))
}

module.exports = { fileStatsFrom }

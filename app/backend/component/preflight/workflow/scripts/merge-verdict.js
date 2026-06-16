const { CHECKS, computeVerdict } = require('../registry.js')
const mk = (c, p) => ({ type: 'check', id: c.id, name: c.name, category: c.category, kind: c.kind, severity: c.severity, status: p.status, summary: p.summary || '', evidence: p.evidence || [], remediation: p.remediation || '' })

// det = runDeterministic output; aiItems = [{id,status,summary,evidence,remediation}] from the agent;
// meta = { pr_number, head_sha } echo (optional) — stamped into the verdict so the app can verify correlation.
function mergeVerdict(det, aiItems, meta) {
  const detById = Object.fromEntries(det.deterministic.map(r => [r.id, r]))
  const todoById = Object.fromEntries((det.todo || []).map(r => [r.id, r]))
  const absentById = Object.fromEntries((det.aiAbsent || []).map(r => [r.id, r]))
  const aiById = Object.fromEntries((aiItems || []).map(r => [r.id, r]))
  const results = []
  for (const c of CHECKS) {
    if (detById[c.id]) { results.push(detById[c.id]); continue }
    if (todoById[c.id]) { results.push(todoById[c.id]); continue }
    if (absentById[c.id]) { results.push(absentById[c.id]); continue }
    if (det.aiChecks.includes(c.id)) {
      const item = aiById[c.id]
      results.push(item ? mk(c, item) : mk(c, { status: 'error', summary: 'The model did not return a result for this check.' }))
    }
  }
  const checklist = { type: 'checklist', checks: det.checklist }
  const verdict = computeVerdict(results)
  const out = { records: [checklist, ...results, verdict] }
  if (meta) out.meta = meta
  return out
}

if (require.main === module) {
  const fs = require('fs')
  const [detPath, aiPath, prPath] = process.argv.slice(2)
  let meta
  // No meta is tolerated (a 2-arg invocation has no pr.json; the app accepts meta-less
  // verdicts for compat) but never silent: the app skips correlation verification without it.
  try { const pr = JSON.parse(fs.readFileSync(prPath, 'utf8')); meta = { pr_number: pr.number, head_sha: pr.headRefOid || '' } } catch (e) { if (prPath) console.error(`merge-verdict: verdict will carry no meta (${prPath} unreadable): ${e.message}`) }
  let det = null
  try { det = JSON.parse(fs.readFileSync(detPath, 'utf8')) } catch {}
  if (!det) {
    // The deterministic phase died before writing its output (e.g. prefetch failure).
    // Emit a fail-loud verdict so the app renders a specific error instead of a missing artifact.
    const records = [
      { type: 'error', title: 'Deterministic phase failed', summary: 'deterministic.json was not produced — the prefetch/checks step failed; see the workflow run logs.' },
      { type: 'verdict', status: 'blocked', counts: { pass: 0, fail: 0, warn: 0, todo: 0, error: 1, skipped: 0 } },
    ]
    const out = { records }
    if (meta) out.meta = meta
    process.stdout.write(JSON.stringify(out))
  } else {
    let aiItems = []
    try { aiItems = fs.readFileSync(aiPath, 'utf8').split('\n').map(l => l.trim()).filter(Boolean).map(l => JSON.parse(l)) } catch {}
    process.stdout.write(JSON.stringify(mergeVerdict(det, aiItems, meta)))
  }
}

module.exports = { mergeVerdict }

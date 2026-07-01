#!/usr/bin/env node
// emit-engine-report.js <cedar.json> <guardians.json> -> stdout engine-report.json
//
// Fuses the two deterministic engines' output into one ground-truth report the security agent reads.
// Each Cedar flag and each Guardians violation becomes one entry with a severity:
//   LOCKED guardrail violation => critical ; tunable deny/automaton => high ; warning => medium.
// Tolerant of missing / "n/a" inputs (=> no violations from that engine). Pure; no I/O beyond argv.
'use strict'
const fs = require('fs')

function readJson(p) { try { return JSON.parse(fs.readFileSync(p, 'utf8')) } catch { return null } }
function sev(locked, isWarning) { return isWarning ? 'medium' : (locked ? 'critical' : 'high') }

function fromCedar(c) {
  if (!c || c.status !== 'ok' || !Array.isArray(c.flags)) return []
  return c.flags.map((f) => ({
    engine: 'cedar', name: f.determining_id, locked: !!f.locked, severity: sev(f.locked, false),
    evidence: `Agent ${f.action} on ${f.resource} denied by policy ${f.determining_id} (tool: ${f.tool}).`,
    ref: f.resource || '',
  }))
}
function fromGuardians(g) {
  if (!g) return []
  const out = []
  for (const v of (g.violations || []))
    out.push({ engine: 'guardians', name: v.name, locked: !!v.locked, severity: sev(v.locked, false),
      evidence: String(v.evidence || `${v.name} (${v.kind})`), ref: v.step || '' })
  for (const w of (g.warnings || []))
    if (typeof w === 'object' && w)
      out.push({ engine: 'guardians', name: w.name || 'advisory', locked: false, severity: 'medium',
        evidence: String(w.evidence || w.name || 'advisory'), ref: w.step || '' })
  return out
}

function main() {
  const violations = [...fromCedar(readJson(process.argv[2])), ...fromGuardians(readJson(process.argv[3]))]
  const summary = { critical: 0, high: 0, medium: 0 }
  for (const v of violations) summary[v.severity] = (summary[v.severity] || 0) + 1
  process.stdout.write(JSON.stringify({ violations, summary }))
}
main()

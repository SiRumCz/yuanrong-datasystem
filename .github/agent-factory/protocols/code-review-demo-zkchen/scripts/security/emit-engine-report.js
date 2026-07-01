#!/usr/bin/env node
// emit-engine-report.js <cedar.json> <guardians.json> [guardians-transcript.json] -> stdout engine-report.json
//
// Fuses the deterministic engines' output into one ground-truth report the security agent reads.
// Each Cedar flag and each Guardians violation becomes one entry with a severity:
//   LOCKED guardrail violation => critical ; tunable deny/automaton => high ; warning => medium.
// Guardians runs over two sources: the PR's PLAN (declared intent) and the captured TRANSCRIPT
// (what the agent actually did). The optional 3rd arg is the transcript verdict; its findings are
// tagged so the agent can tell "actual behaviour" apart from "declared plan".
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
// source: 'plan' (default) or 'transcript'. Transcript findings are the agent's ACTUAL behaviour,
// so tag the name and prefix the evidence to keep them distinguishable in the fused report.
function fromGuardians(g, source = 'plan') {
  if (!g) return []
  const tx = source === 'transcript'
  const tagName = (nm) => tx ? `${nm}@transcript` : nm
  const tagEv = (ev) => tx ? `actual behaviour (transcript): ${ev}` : ev
  const out = []
  for (const v of (g.violations || []))
    out.push({ engine: 'guardians', source, name: tagName(v.name), locked: !!v.locked, severity: sev(v.locked, false),
      evidence: tagEv(String(v.evidence || `${v.name} (${v.kind})`)), ref: v.step || '' })
  for (const w of (g.warnings || []))
    if (typeof w === 'object' && w)
      out.push({ engine: 'guardians', source, name: tagName(w.name || 'advisory'), locked: false, severity: 'medium',
        evidence: tagEv(String(w.evidence || w.name || 'advisory')), ref: w.step || '' })
  return out
}

function main() {
  const violations = [
    ...fromCedar(readJson(process.argv[2])),
    ...fromGuardians(readJson(process.argv[3]), 'plan'),
    ...fromGuardians(readJson(process.argv[4]), 'transcript'),
  ]
  const summary = { critical: 0, high: 0, medium: 0 }
  for (const v of violations) summary[v.severity] = (summary[v.severity] || 0) + 1
  process.stdout.write(JSON.stringify({ violations, summary }))
}
main()

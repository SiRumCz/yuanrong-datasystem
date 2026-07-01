#!/usr/bin/env node
// anchor-engine-findings.js <engine-report.json> <pr.diff> <evidence.json>
//
// DETERMINISTIC GATE (no agent dependency): rewrites evidence.json in place so each LOCKED engine
// violation becomes a `critical` security finding in findings[], anchored to a REAL added (RIGHT-side)
// line from pr.diff — which is exactly what the protocol's review-findings-anchored check requires —
// and sets verdict REQUEST_CHANGES. The full report is always recorded under engine_report. If the
// agent never wrote evidence, a minimal valid security evidence is created so the engines still gate.
'use strict'
const fs = require('fs')

function readJson(p, dflt) { try { return JSON.parse(fs.readFileSync(p, 'utf8')) } catch { return dflt } }

// First added RIGHT-side line in a unified diff -> { path, line } (or null if the diff adds nothing).
// Mirrors how review-findings-anchored / _diff.py treat RIGHT-side lines: the new-file line number of
// the first `+` line inside a hunk.
function firstAddedLine(diffText) {
  let path = null, newLine = 0, inHunk = false
  for (const ln of String(diffText || '').split('\n')) {
    if (ln.startsWith('+++ ')) {
      const m = ln.match(/^\+\+\+ b\/(.+)$/) || ln.match(/^\+\+\+ (.+)$/)
      path = m ? m[1].replace(/\t.*$/, '') : null
      inHunk = false
      continue
    }
    const h = ln.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
    if (h) { newLine = parseInt(h[1], 10); inHunk = true; continue }
    if (!inHunk || !path || path === '/dev/null') continue
    if (ln.startsWith('+')) return { path, line: newLine }   // first added line on the RIGHT
    if (ln.startsWith('-')) continue                          // left-only: RIGHT line number unchanged
    if (ln.startsWith('\\')) continue                         // "\ No newline at end of file"
    newLine++                                                 // context line advances the RIGHT counter
  }
  return null
}

function main() {
  const report = readJson(process.argv[2], { violations: [] }) || { violations: [] }
  let diffText = ''
  try { diffText = fs.readFileSync(process.argv[3], 'utf8') } catch { diffText = '' }
  const evPath = process.argv[4]

  let ev = readJson(evPath, null)
  if (!ev || typeof ev !== 'object' || Array.isArray(ev)) ev = { dimension: 'security', verdict: 'APPROVE', findings: [] }
  if (ev.dimension !== 'security') ev.dimension = 'security'
  if (!Array.isArray(ev.findings)) ev.findings = []

  const locked = (report.violations || []).filter((v) => v && v.locked)
  const anchor = firstAddedLine(diffText)
  let added = 0
  if (anchor) {
    for (const v of locked) {
      ev.findings.push({
        path: anchor.path,
        line: anchor.line,
        severity: 'critical',
        category: 'security',
        title: `[engine:${v.engine}] ${v.name}`,
        impact: `${String(v.evidence || v.name)} — deterministic ${v.engine} engine violation (anchored to a changed line for review).`,
        fix: 'Remove the unsafe data flow / disallowed agent action this change enables; if intended, tune the corresponding non-LOCKED policy.',
      })
      added++
    }
  }
  if (added > 0) ev.verdict = 'REQUEST_CHANGES'
  ev.engine_report = report
  if (locked.length && !anchor) ev.engine_report.unanchored = true

  try { fs.writeFileSync(evPath, JSON.stringify(ev)) } catch (e) { process.stderr.write(`anchor: write failed: ${e}\n`) }
  process.stderr.write(`anchor: injected ${added}/${locked.length} LOCKED engine finding(s)${anchor ? '' : ' (no anchorable added line)'}\n`)
}
main()

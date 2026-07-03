#!/usr/bin/env node
// transcript-extract.js <transcripts-dir> -> stdout a Guardians Workflow AST { steps: [...] }.
//
// The de-facto "plan" of what the agent ACTUALLY did, lifted from the captured transcript so
// Guardians can taint-verify real behaviour (not just the declared plan prose that plan-extract.js
// scans). Reuses the same tool_use iterator + PARC classifier Cedar uses (actions-from-transcript.js),
// then models the LOCKED taint flows the policy targets:
//   read_secret -> network_send.body   (no_secret_exfiltration)
//   read_secret -> write_file.content  (no_secret_to_disk)
// Conservative, mirroring plan-extract.js: a sink is only emitted once a secret has actually been
// read (sticky), so benign egress / reads produce no steps. Benign egress and destructive commands
// are Cedar's per-action domain and are intentionally NOT emitted here (no double-flagging).
'use strict'
const fs = require('fs')
const path = require('path')
const { toolUses, toAction } = require('./_transcript-actions.js')

// Read every .jsonl in the dir, ordered by filename (locate.js writes 000.jsonl, 001.jsonl, …
// chronologically). Missing/unreadable dir => no transcript => empty step list (Guardians ok).
function readTranscripts(dir) {
  let files
  try { files = fs.readdirSync(dir).filter((f) => f.endsWith('.jsonl')).sort() }
  catch { return [] }
  return files.map((f) => {
    try { return fs.readFileSync(path.join(dir, f), 'utf8') } catch { return '' }
  })
}

function main() {
  const dir = process.argv[2] || ''
  const steps = []
  let touchedSecret = false
  let lastSecret = null
  let n = 0
  for (const text of readTranscripts(dir)) {
    for (const { name, input } of toolUses(text)) {
      const a = toAction(name, input)
      if (!a) continue
      if (a.action === 'ReadSecret') {
        const sym = `s${n++}`
        steps.push({ tool: 'read_secret', args: {}, result: sym })
        touchedSecret = true
        lastSecret = sym
      } else if (a.action === 'Network' && a.external_host && touchedSecret) {
        // A secret was read earlier in the session and data then left to an external host.
        steps.push({ tool: 'network_send', args: { host: a.resource || 'external', body: { $ref: lastSecret } } })
      } else if (a.action === 'WriteFile' && touchedSecret) {
        // A secret was read earlier and content was then written to disk.
        steps.push({ tool: 'write_file', args: { path: a.resource || '', content: { $ref: lastSecret } } })
      }
    }
  }
  process.stdout.write(JSON.stringify({ steps }))
}
main()

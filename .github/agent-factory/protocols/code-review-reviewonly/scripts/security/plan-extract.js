#!/usr/bin/env node
// plan-extract.js <plan.txt> -> stdout a Guardians Workflow AST { steps: [...] }.
//
// v1 DETERMINISTIC HEURISTIC (no LLM, no network): scans the plan's prose for declared/injected
// effects and emits the corresponding effect-tool nodes so Guardians can verify the data flow.
// It is intentionally conservative — it models the unsafe patterns the LOCKED taint rules target
// (secret read -> external sink; injected instruction -> command/network). A richer LLM-assisted
// extraction is a documented follow-up.
'use strict'
const fs = require('fs')

const SECRET_RE = /\b(\.env|secrets?\b|credentials?\b|api[_-]?keys?\b|passwords?\b|\.pem\b|\.key\b|token)\b/i
const URL_RE = /\bhttps?:\/\/([^\s/"')<>]+)/i
const EGRESS_RE = /\b(POST|send (?:it|them|this|the)|upload|exfiltrat|transmit|curl|fetch|webhook|ingest|collector)\b/i
// Injected-instruction signals: imperative directives hidden in the plan that an implementing agent
// might follow (prompt injection in the data), per Meijer's threat model.
const INJECT_RE = /(do not (?:tell|mention|inform|notify)|ignore (?:all )?previous|without (?:the user'?s? )?(?:consent|knowledge)|hidden (?:step|instruction)|<!--[\s\S]*?(?:must|always|also|do not)[\s\S]*?-->)/i

function main() {
  let text = ''
  try { text = fs.readFileSync(process.argv[2] || '', 'utf8') } catch { text = '' }
  const steps = []
  const hasSecret = SECRET_RE.test(text)
  const hasEgress = EGRESS_RE.test(text) || URL_RE.test(text)
  const hasInject = INJECT_RE.test(text)
  const host = (text.match(URL_RE) || [])[1] || 'external'

  if (hasInject) steps.push({ tool: 'read_external', args: { ref: 'plan-injected-instruction' }, result: 'instr' })
  if (hasSecret) steps.push({ tool: 'read_secret', args: {}, result: 's' })
  // Designed exfiltration: a secret read whose data is sent to an external sink.
  if (hasSecret && hasEgress) steps.push({ tool: 'network_send', args: { host, body: { $ref: 's' } } })
  // Injected instruction wired into an executable/egress sink (data-became-code).
  if (hasInject && hasEgress) steps.push({ tool: 'network_send', args: { host, body: { $ref: 'instr' } } })
  if (hasInject) steps.push({ tool: 'run_command', args: { argv: { $ref: 'instr' } } })

  process.stdout.write(JSON.stringify({ steps }))
}
main()

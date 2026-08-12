#!/usr/bin/env node
'use strict'
// decide.js — thin driver for the LIVE (PreToolUse) Cedar call site.
//
// CLI:    node decide.js <request.json> <policy-dir>
// stdin:  alternatively pass "-" as <request.json> to read the request from stdin.
// stdout: { "decision": "Allow"|"Deny", "determining": [ "<policy id>", ... ],
//           "descriptions": { "<policy id>": "<@description>" },
//           "policy_count": <n> }
// exit:   0 on a decision; 1 on any failure (missing dir, bad JSON, cedar error).
//         The caller (hook.py) treats a non-zero exit as "engine unreachable" and
//         applies its configured failure posture.
//
// This file does NOT call cedar.isAuthorized: the one authorize seam is
// ../_cedar-decide.js (decideDetailed). No schema is loaded — Cedar evaluates
// without one and `entities` is `[]` because no policy in policy/cedar/live
// dereferences an entity attribute (see that directory's README).
const fs = require('node:fs')
const path = require('node:path')
const { decideDetailed } = require('../_cedar-decide.js')

// Cedar policy ids: cedar-wasm ignores `@id("…")` when the policy set is one
// concatenated string and numbers the policies instead (policy0, policy1, …).
// Passing a { id: text } map makes the map key the policy id, so
// `diagnostics.reason` comes back as real, greppable policy names. Verified
// against @cedar-policy/cedar-wasm 4.11.2.
const ID_RE = /@id\(\s*"([^"]+)"\s*\)/
const DESC_RE = /@description\(\s*"((?:[^"\\]|\\.)*)"\s*\)/

/** loadPolicies(dir) -> { policies: {id: text}, descriptions: {id: desc} } */
function loadPolicies (dir) {
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.cedar')).sort()
  if (!files.length) throw new Error('no .cedar policies in ' + dir)
  const policies = {}
  const descriptions = {}
  for (const f of files) {
    const text = fs.readFileSync(path.join(dir, f), 'utf8')
    const m = text.match(ID_RE)
    // Fall back to the filename stem so an un-annotated policy still gets a
    // stable, human-meaningful id rather than a positional one.
    const id = (m && m[1]) || path.basename(f, '.cedar')
    if (policies[id]) throw new Error('duplicate policy id "' + id + '" (' + f + ')')
    policies[id] = text
    const d = text.match(DESC_RE)
    if (d) descriptions[id] = d[1].replace(/\\(.)/g, '$1')
  }
  return { policies, descriptions }
}

function main (argv) {
  const [requestArg, policyDir] = argv
  if (!requestArg || !policyDir) {
    throw new Error('usage: decide.js <request.json|-> <policy-dir>')
  }
  const raw = requestArg === '-'
    ? fs.readFileSync(0, 'utf8')
    : fs.readFileSync(requestArg, 'utf8')
  const request = JSON.parse(raw)
  const { policies, descriptions } = loadPolicies(policyDir)

  const r = decideDetailed(policies, [], request)
  // A policy whose condition errored was SKIPPED by Cedar, so a forbid may have
  // been silently dropped. Refuse to answer rather than under-report.
  if (r.errors.length) throw new Error('cedar policy evaluation errors: ' + r.errors.join('; '))

  const determining = r.determining
  const out = {
    decision: r.decision,
    determining,
    descriptions: Object.fromEntries(
      determining.filter(id => descriptions[id]).map(id => [id, descriptions[id]])),
    policy_count: Object.keys(policies).length,
  }
  process.stdout.write(JSON.stringify(out) + '\n')
}

if (require.main === module) {
  try {
    main(process.argv.slice(2))
  } catch (err) {
    process.stderr.write('decide.js: ' + (err && err.message ? err.message : String(err)) + '\n')
    process.exit(1)
  }
}

module.exports = { loadPolicies }

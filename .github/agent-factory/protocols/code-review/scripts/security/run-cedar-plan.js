'use strict'
// run-cedar-plan.js — run Cedar (PARC authorization) over a plan_ast instead of a transcript.
//
// CLI: node run-cedar-plan.js <plan_ast.json> <cedar-default-dir> [custom-dir] [changedPathsJson]
// Output (stdout): { "verdict": "pass"|"fail"|"n/a", "status", "flags": [...] }
//
// Each plan_ast step {tool, args, result} is mapped to a PARC action descriptor and
// authorized through the SAME Cedar policy + decide() path as the transcript analyzer;
// DENYs become flags. The tool vocabulary is abstract (readDiff/curlPost/…), so this
// adapter classifies by tool-name shape + arg keys — the plan_ast analogue of
// _transcript-actions.toAction. Reuses policy-merge.js + _cedar-decide.js verbatim.
const fs = require('node:fs')
const path = require('node:path')
const { mergeCedar } = require('./policy-merge.js')
const { decide } = require('./_cedar-decide.js')

// Single source of truth for tool-name -> canonical mapping (shared with guardians' _toolmap.py)
const _MAP = JSON.parse(fs.readFileSync(path.join(__dirname, 'plan-tool-aliases.json'), 'utf8'))
const _SECRET_RE = new RegExp(_MAP.secret_path_re, 'i')
const _ALIAS = {}
for (const [canon, al] of Object.entries(_MAP.aliases)) for (const a of al) _ALIAS[a] = canon
const DESTRUCTIVE_RE = /\brm\s+-rf\b|git\s+push\s+--force|git\s+reset\s+--hard|\bmkfs\b|:\s*>\s*\//

function litArg(args, keys) {  // first literal string arg among the given param names
  for (const k of Object.keys(args || {})) {
    if (keys.includes(k.toLowerCase()) && typeof args[k] === 'string') return args[k]
  }
  return ''
}

function canonical(tool, args) {  // mirrors _toolmap.canonical
  const t = String(tool || '').toLowerCase().replace(/[^a-z0-9]/g, '')
  let canon = _ALIAS[t]
  if (canon === undefined) {
    for (const [a, c] of Object.entries(_ALIAS)) { if (t.startsWith(a)) { canon = c; break } }
  }
  if (canon === 'read') {
    const p = Object.values(args || {}).filter(v => typeof v === 'string').join(' ')
    return _SECRET_RE.test(p) ? 'read_secret' : 'read_repo_file'
  }
  return canon
}

// plan_ast step -> PARC-ish action (or null to skip a non-security-relevant step)
function stepToAction(tool, args) {
  args = args || {}
  const c = canonical(tool, args)
  if (c === 'read_secret')
    return { action: 'ReadSecret', resourceType: 'Secret', resource: litArg(args, ['path', 'file', 'filename']) || String(tool), touched_secret: true }
  if (c === 'read_repo_file' || c === 'read_external')
    return { action: 'ReadFile', resourceType: 'File', resource: litArg(args, ['path', 'file', 'filename', 'pr', 'issue', 'query']) || String(tool) }
  if (c === 'network_send') {
    let host = litArg(args, ['url', 'host', 'endpoint', 'to'])
    try { const h = new URL(host).host; if (h) host = h } catch { /* not a URL */ }
    return { action: 'Network', resourceType: 'Host', resource: host || 'unknown', external_host: true }
  }
  if (c === 'publish') {  // external output channel (issue/PR/comment/file) — an egress sink
    const ch = litArg(args, ['channel', 'to', 'target', 'repo', 'issue', 'url'])
    return { action: 'Network', resourceType: 'Host', resource: ch || 'publish', external_host: true }
  }
  if (c === 'write_file')
    return { action: 'WriteFile', resourceType: 'File', resource: litArg(args, ['path', 'file', 'filename']) }
  if (c === 'run_command') {
    const cmd = litArg(args, ['command', 'argv', 'cmd', 'script'])
    return { action: 'RunCommand', resourceType: 'Command', resource: 'bash', destructive: DESTRUCTIVE_RE.test(cmd) }
  }
  return null  // compute / unrecognized -> not security-relevant
}

function analyzePlan(plan, { policiesText, allowedHosts = [], changedPaths = [] }) {
  const steps = (plan && Array.isArray(plan.steps)) ? plan.steps : null
  if (!steps || !steps.length) return { status: 'n/a', flags: [] }
  let touchedSecret = false  // sticky: once a secret is read, later egress is exfiltration
  const flags = []
  const lockedIds = new Set(['locked.no-exfiltration', 'locked.no-destructive'])
  for (const s of steps) {
    if (!s || typeof s !== 'object') continue
    const a = stepToAction(s.tool, s.args); if (!a) continue
    if (a.touched_secret) touchedSecret = true
    const context = {
      touched_secret: touchedSecret,
      external_host: !!a.external_host,
      destructive: !!a.destructive,
      in_changed_set: a.resourceType === 'File' ? changedPaths.includes(a.resource) : true,
      in_repo: a.resourceType === 'File',
      allowed_hosts: allowedHosts,
      host: a.action === 'Network' ? a.resource : '',
    }
    const request = {
      principal: 'Agent::"session"',
      action: `Action::"${a.action}"`,
      resource: `${a.resourceType}::"${a.resource}"`,
      context,
    }
    if (decide(policiesText, '[]', request) === 'Deny') {
      const determining_id =
        (context.touched_secret && context.external_host) ? 'locked.no-exfiltration' :
        a.destructive ? 'locked.no-destructive' :
        a.action === 'WriteFile' ? 'scope.writes-in-changed-set' :
        a.action === 'Network' ? 'net.egress-allowlist' : 'unknown'
      // Only LOCKED violations (exfiltration / destructive) count — the repo convention.
      // The abstract plan-tool vocabulary doesn't map to the scope/read permits, so
      // non-LOCKED denies are false positives on review plans; drop them.
      if (lockedIds.has(determining_id)) {
        flags.push({ tool: s.tool, action: a.action, resource: a.resource, determining_id, locked: true })
      }
    }
  }
  return { status: 'ok', flags }
}

const [, , planFile, defaultDir, customDir, changedPathsJson] = process.argv
const plan = JSON.parse(fs.readFileSync(planFile, 'utf8'))
const { policiesText } = mergeCedar(defaultDir, customDir || null)
const changedPaths = JSON.parse(changedPathsJson || '[]')
const r = analyzePlan(plan, { policiesText, allowedHosts: [], changedPaths })
const verdict = r.status === 'n/a' ? 'n/a' : (r.flags.length ? 'fail' : 'pass')
console.log(JSON.stringify({ verdict, status: r.status, flags: r.flags }))

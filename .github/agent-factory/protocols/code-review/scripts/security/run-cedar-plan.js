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
const { mergeCedar } = require('./policy-merge.js')
const { decide } = require('./_cedar-decide.js')

const SECRET_RE = /(^|\/)\.env|\.pem$|credentials|secret|\.key$|token/i
const DESTRUCTIVE_RE = /\brm\s+-rf\b|git\s+push\s+--force|git\s+reset\s+--hard|\bmkfs\b|:\s*>\s*\//
const NET = /(curlpost|networksend|httppost|upload|^post|^curl|send$)/i
const FILE_WRITE = /(writefile|savefile|^write)/i
const CMD = /(^bash|^run|exec|shell|^command)/i
const READ = /^(read|fetch|load|get|search|web|tavily)/i

function litArg(args, keys) {  // first literal string arg among the given param names
  for (const k of Object.keys(args || {})) {
    if (keys.includes(k.toLowerCase()) && typeof args[k] === 'string') return args[k]
  }
  return ''
}

// plan_ast step -> PARC-ish action (or null to skip a non-security-relevant step)
function stepToAction(tool, args) {
  const t = String(tool || '').toLowerCase()
  args = args || {}
  if (NET.test(t)) {
    let host = litArg(args, ['url', 'host', 'endpoint', 'to'])
    try { const h = new URL(host).host; if (h) host = h } catch { /* not a URL; use as-is */ }
    return { action: 'Network', resourceType: 'Host', resource: host || 'unknown', external_host: true }
  }
  if (FILE_WRITE.test(t)) {
    return { action: 'WriteFile', resourceType: 'File', resource: litArg(args, ['path', 'file', 'filename']) }
  }
  if (CMD.test(t)) {
    const cmd = litArg(args, ['command', 'argv', 'cmd', 'script'])
    return { action: 'RunCommand', resourceType: 'Command', resource: 'bash', destructive: DESTRUCTIVE_RE.test(cmd) }
  }
  if (READ.test(t)) {
    const p = litArg(args, ['path', 'file', 'filename', 'pr', 'issue', 'url', 'query'])
    return (SECRET_RE.test(p) || SECRET_RE.test(t))
      ? { action: 'ReadSecret', resourceType: 'Secret', resource: p || t, touched_secret: true }
      : { action: 'ReadFile', resourceType: 'File', resource: p || t }
  }
  return null
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

// app/backend/component/reviewers/workflow/security/actions-from-transcript.js
'use strict'
const { decide } = require('./_cedar-decide.js')

const SECRET_RE = /(^|\/)\.env|\.pem$|credentials|secret|\.key$/i
const DESTRUCTIVE_RE = /\brm\s+-rf\b|git\s+push\s+--force|git\s+reset\s+--hard|\bmkfs\b|:\s*>\s*\//

// Map one Claude Code tool_use entry to a PARC-ish action descriptor (or null to skip).
function toAction(name, input) {
  const n = String(name || '')
  if (n === 'Read') {
    const p = input.file_path || ''
    return SECRET_RE.test(p)
      ? { action: 'ReadSecret', resourceType: 'Secret', resource: p, touched_secret: true }
      : { action: 'ReadFile', resourceType: 'File', resource: p }
  }
  if (n === 'Edit' || n === 'Write' || n === 'NotebookEdit')
    return { action: 'WriteFile', resourceType: 'File', resource: input.file_path || input.notebook_path || '' }
  if (n === 'WebFetch') {
    let host
    try { host = new URL(input.url).host } catch { return null }
    if (!host) return null
    return { action: 'Network', resourceType: 'Host', resource: host, external_host: true }
  }
  if (n === 'WebSearch') return null  // no destination host; not a network egress action
  if (n === 'Bash')
    return { action: 'RunCommand', resourceType: 'Command', resource: 'bash',
             destructive: DESTRUCTIVE_RE.test(input.command || '') }
  return null
}

function* toolUses(jsonlText) {
  for (const line of jsonlText.split('\n')) {
    if (!line.trim()) continue
    let obj; try { obj = JSON.parse(line) } catch { continue }
    const content = obj && obj.message && obj.message.content
    if (!Array.isArray(content)) continue
    for (const c of content) if (c && c.type === 'tool_use') yield { name: c.name, input: c.input || {} }
  }
}

// analyzeTranscripts(jsonlTexts[], { policiesText, allowedHosts, changedPaths }) -> { status, flags }
function analyzeTranscripts(jsonlTexts, { policiesText, allowedHosts = [], changedPaths = [] }) {
  if (!jsonlTexts || !jsonlTexts.length) return { status: 'n/a', flags: [] }
  let touchedSecret = false  // sticky across the session: once a secret is read, egress is exfil
  const flags = []
  const lockedIds = new Set(['locked.no-exfiltration', 'locked.no-destructive'])
  for (const text of jsonlTexts) {
    for (const { name, input } of toolUses(text)) {
      const a = toAction(name, input); if (!a) continue
      if (a.touched_secret) touchedSecret = true
      const context = {
        touched_secret: touchedSecret,
        external_host: !!a.external_host,
        destructive: !!a.destructive,
        in_changed_set: a.resourceType === 'File' ? changedPaths.includes(a.resource) : true,
        in_repo: a.resourceType === 'File',
        allowed_hosts: allowedHosts,
        // Task 3 fix: net.egress-allowlist permit gates on context.allowed_hosts.contains(context.host)
        // so host must be present for every request; non-Network actions get ''.
        host: a.action === 'Network' ? a.resource : '',
      }
      const request = {
        principal: 'Agent::"session"',
        action: `Action::"${a.action}"`,
        resource: `${a.resourceType}::"${a.resource}"`,
        context,
      }
      if (decide(policiesText, '[]', request) === 'Deny') {
        // Attribute the deny to the most security-relevant rule deterministically from the action shape.
        const determining_id =
          (context.touched_secret && context.external_host) ? 'locked.no-exfiltration' :
          a.destructive ? 'locked.no-destructive' :
          a.action === 'WriteFile' ? 'scope.writes-in-changed-set' :
          a.action === 'Network' ? 'net.egress-allowlist' : 'unknown'
        flags.push({ tool: name, action: a.action, resource: a.resource, context,
                     determining_id, locked: lockedIds.has(determining_id) })
      }
    }
  }
  return { status: 'ok', flags }
}

module.exports = { analyzeTranscripts, toAction }

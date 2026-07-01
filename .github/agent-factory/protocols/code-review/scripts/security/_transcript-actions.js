'use strict'
// Pure transcript classification — NO engine dependency (no Cedar wasm, no Guardians). Shared by
// Cedar's analyzer (actions-from-transcript.js) and Guardians' transcript-extract.js so both read the
// captured tool calls the same way. Keep this side-effect-free and dependency-free.

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

// Yield every tool_use block across a .jsonl transcript, in order.
function* toolUses(jsonlText) {
  for (const line of jsonlText.split('\n')) {
    if (!line.trim()) continue
    let obj; try { obj = JSON.parse(line) } catch { continue }
    const content = obj && obj.message && obj.message.content
    if (!Array.isArray(content)) continue
    for (const c of content) if (c && c.type === 'tool_use') yield { name: c.name, input: c.input || {} }
  }
}

module.exports = { toAction, toolUses, SECRET_RE, DESTRUCTIVE_RE }

'use strict'
// Build a context-viewer SessionExport from the parts driver output + the agent's
// phase classification + pr.json (for the meta echo). Mirrors preflight/merge-verdict.js:
// always emits SOMETHING (a minimal error export when inputs are missing), never absent.
const fs = require('fs')

const PALETTE = {
  UNDERSTAND: '#60a5fa', EXPLORE: '#2dd4bf', ANALYZE: '#fb7185', PLAN: '#fbbf24',
  IMPLEMENT: '#8b5cf6', VERIFY: '#a3e635', COMPLETE: '#94a3b8',
}
const PHASES = Object.keys(PALETTE)
const DEFAULT_PHASE = 'COMPLETE'

function readJson(p) { try { return JSON.parse(fs.readFileSync(p, 'utf8')) } catch { return null } }
function readJsonl(p) { try { return fs.readFileSync(p, 'utf8').split('\n').filter((l) => l.trim()).map((l) => { try { return JSON.parse(l) } catch { return null } }).filter(Boolean) } catch { return [] } }

function metaFromPr(pr) { return pr ? { pr_number: pr.number, head_sha: pr.headRefOid || '' } : null }
function normalizePhase(p) { const up = String(p || '').toUpperCase().split('.')[0].trim(); return PHASES.includes(up) ? up : DEFAULT_PHASE }

function errorExport(meta, summary) {
  return { version: '1.0', exportedAt: new Date().toISOString(), files: [], groups: [], analytics: { componentComparison: [] }, meta: meta || undefined, error: { title: 'Context export failed', summary } }
}

function build(parts, phasesList, pr) {
  const meta = metaFromPr(pr)
  if (!parts || !Array.isArray(parts.messages) || !parts.messages.length) {
    return errorExport(meta, 'No transcript was found for this PR (commit a .conversations/<owner>/<repo>/pr-<number>/<session>.jsonl file, or enable the capture hook).')
  }
  const phaseOf = new Map((phasesList || []).map((p) => [String(p.id), normalizePhase(p.phase)]))
  const messages = parts.messages.map((m) => ({
    id: String(m.id),
    role: m.role,
    parts: (m.parts || []).map((part) => ({ ...part, id: String(part.id), component: phaseOf.get(String(part.id)) || DEFAULT_PHASE })),
  }))
  const componentTokens = {}
  let totalTokens = 0
  for (const m of messages) for (const part of m.parts) {
    const tok = typeof part.token_count === 'number' ? part.token_count : 0
    totalTokens += tok
    componentTokens[part.component] = (componentTokens[part.component] || 0) + tok
  }
  const colors = {}
  for (const ph of Object.keys(componentTokens)) colors[ph] = PALETTE[ph] || '#94a3b8'
  const branch = (pr && pr.headRefName) || 'session'
  const fileId = `ctx-${(pr && pr.number) || '0'}`
  const filename = (parts.meta && parts.meta.filename) || `${branch}.jsonl`
  const turnCount = messages.filter((m) => m.role === 'user').length
  return {
    version: '1.0',
    exportedAt: new Date().toISOString(),
    files: [{
      id: fileId,
      filename,
      title: `PR #${(pr && pr.number) || '?'} — ${branch}`,
      conversation: { messages },
      colors,
      summary: null,
      analysis: null,
      metadata: { parserName: 'Context Viewer', agent: 'claude-code', model: (parts.meta && parts.meta.model) || 'unknown' },
    }],
    groups: [],
    analytics: { componentComparison: [{ fileId, filename, totalTokens, turnCount, messageCount: messages.length, componentTokens }] },
    meta: meta || undefined,
  }
}

module.exports = { build, metaFromPr, normalizePhase, errorExport, PALETTE, PHASES }

if (require.main === module) {
  const [partsPath, phasesPath, prPath] = process.argv.slice(2)
  process.stdout.write(JSON.stringify(build(readJson(partsPath), readJsonl(phasesPath), readJson(prPath))))
}

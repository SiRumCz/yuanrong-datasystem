// app/backend/component/reviewers/workflow/security/policy-merge.js
const fs = require('node:fs'); const path = require('node:path')

const LOCKED_IDS = ['locked.no-exfiltration', 'locked.no-destructive']

function idsOf(text) {
  const ids = []; const re = /@id\(\s*"([^"]+)"\s*\)/g; let m
  while ((m = re.exec(text))) ids.push(m[1])
  return ids
}
function readDir(dir) {
  if (!dir || !fs.existsSync(dir)) return []
  return fs.readdirSync(dir).filter(f => f.endsWith('.cedar')).sort()
    .map(f => ({ name: f, text: fs.readFileSync(path.join(dir, f), 'utf8') }))
}

// mergeCedar(defaultDir, customDir|null) -> { policiesText, warnings }
function mergeCedar(defaultDir, customDir) {
  const warnings = []
  const parts = readDir(defaultDir).map(p => p.text)
  for (const c of readDir(customDir)) {
    const conflict = idsOf(c.text).find(id => LOCKED_IDS.includes(id))
    if (conflict) { warnings.push(`policy_conflict: custom ${c.name} reuses LOCKED id "${conflict}" — dropped`); continue }
    parts.push(c.text)
  }
  return { policiesText: parts.join('\n'), warnings }
}
module.exports = { mergeCedar, LOCKED_IDS, idsOf }

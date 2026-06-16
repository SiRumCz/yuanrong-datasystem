const ARTIFACT_MAX_CHARS = 12000
const SPEC_PROBE = ['docs/specs', 'docs/superpowers/specs', 'specs', 'SPEC.md', 'REQUIREMENTS.md']
const PLAN_PROBE = ['docs/superpowers/plans', 'docs/plans', 'plans', 'PLAN.md']

function detectSpecInBody(body) {
  if (!body) return null
  const m = body.match(/^#{1,6}\s*(requirements?|spec(?:ification)?)\b.*$/im)
  if (!m) return null
  const after = body.slice(body.indexOf(m[0]) + m[0].length)
  const section = (after.split(/^#{1,6}\s/m)[0] || '')
  if (!/\S/.test(section)) return null
  return m[0].trim()
}
function detectPlanInBody(body) {
  if (!body) return null
  const heading = body.match(/^#{1,6}\s*(implementation\s+plan|plan)\b.*$/im)
  if (heading) return heading[0].trim()
  if (/^\s*[-*]\s+\[[ xX]\]\s+.+$/m.test(body)) return 'task checklist in PR description'
  return null
}
function classifyArtifactPaths(paths) {
  const spec = [], plan = []
  for (const p of paths) {
    if (/(^|\/)docs\/(superpowers\/)?specs\//i.test(p) || /(^|\/)(SPEC|REQUIREMENTS)\.md$/i.test(p) || /(^|\/)specs?\//i.test(p)) spec.push(p)
    if (/(^|\/)docs\/(superpowers\/)?plans?\//i.test(p) || /(^|\/)PLAN\.md$/i.test(p) || /(^|\/)plans?\//i.test(p)) plan.push(p)
  }
  return { spec, plan }
}

async function probePaths(probes, classify, probe) {
  const found = []; let errored = false
  for (const p of probes) {
    let entries
    try { entries = await probe(p) } catch { errored = true; continue }
    if (!entries) continue
    for (const e of entries) if (e.type === 'file') {
      const { spec, plan } = classifyArtifactPaths([e.path])
      if (classify === 'spec' ? spec.length : plan.length) found.push(e.path)
    }
  }
  return { found, errored }
}

// inputs: { body, changedPaths }; io: { probe(dir)->entries|null, readFile(path)->text|null }
async function locateArtifact(kind, { body, changedPaths }, { probe, readFile }) {
  const bodyHit = kind === 'spec' ? detectSpecInBody(body) : detectPlanInBody(body)
  const { spec, plan } = classifyArtifactPaths(changedPaths || [])
  const changedHits = kind === 'spec' ? spec : plan
  // The repo probe is kept ONLY to surface a read failure (errored) and to report where we looked —
  // its hits do NOT make a doc "this PR's artifact". An artifact counts as ASSOCIATED with the PR
  // only when the PR brings it in its own diff (changedHits) or writes it into the body (bodyHit).
  // Attributing an arbitrary repo doc (formerly pr.found[0]) blocked unrelated PRs — e.g. a CI /
  // registration PR judged against whatever spec/plan happened to sort first under docs/.
  const pr = await probePaths(kind === 'spec' ? SPEC_PROBE : PLAN_PROBE, kind, probe)
  const evidence = []
  if (bodyHit) evidence.push({ label: 'PR body', detail: bodyHit })
  for (const p of changedHits) evidence.push({ label: kind === 'spec' ? 'spec file' : 'plan file', detail: p, location: p })
  let text = ''
  if (changedHits[0]) { const raw = await readFile(changedHits[0]); if (raw) text = raw.slice(0, ARTIFACT_MAX_CHARS) }
  if (!text && bodyHit && body) { const idx = body.indexOf(bodyHit); text = body.slice(idx, idx + ARTIFACT_MAX_CHARS) }
  const searched = ['PR body', 'the PR diff', ...(kind === 'spec' ? SPEC_PROBE : PLAN_PROBE)]
  if (evidence.length) return { found: true, evidence, text }
  if (pr.errored) return { found: false, error: true, searched }
  return { found: false, searched }
}

module.exports = { detectSpecInBody, detectPlanInBody, classifyArtifactPaths, locateArtifact, SPEC_PROBE, PLAN_PROBE }

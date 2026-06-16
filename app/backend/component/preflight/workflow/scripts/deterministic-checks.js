const { CHECKS, AI_CHECK_ARTIFACT } = require('../registry.js')
const { DETERMINISTIC } = require('../checks.js')
const { locateArtifact } = require('./locate.js')

const mk = (c, p) => ({ type: 'check', id: c.id, name: c.name, category: c.category, kind: c.kind, severity: c.severity, status: p.status, summary: p.summary || '', evidence: p.evidence || [], remediation: p.remediation || '' })

// pr: { number,title,body,files:[{filename,status,additions,deletions}] }; io: { probe, readFile }
async function runDeterministic(pr, diff, io) {
  const changedFiles = pr.files || []
  const changedPaths = changedFiles.filter(f => f.status !== 'removed').map(f => f.filename)
  const artifacts = {
    spec: await locateArtifact('spec', { body: pr.body || '', changedPaths }, io),
    plan: await locateArtifact('plan', { body: pr.body || '', changedPaths }, io),
  }
  const context = { changedFiles, artifacts }
  const checklist = CHECKS.map(c => ({ id: c.id, name: c.name, category: c.category, kind: c.kind, severity: c.severity, state: c.state }))
  const deterministic = [], todo = [], aiChecks = [], aiAbsent = []
  for (const c of CHECKS) {
    if (c.state === 'todo') { todo.push(mk(c, { status: 'todo', summary: 'Not yet implemented.' })); continue }
    if (c.kind === 'deterministic') {
      try { deterministic.push(mk(c, await DETERMINISTIC[c.id](context))) } catch (e) { deterministic.push(mk(c, { status: 'error', summary: String(e && e.message || e) })) }
      continue
    }
    const key = AI_CHECK_ARTIFACT[c.id]
    const art = key ? artifacts[key] : null
    if (key && !(art && art.found)) {
      if (art && art.error) aiAbsent.push(mk(c, { status: 'error', summary: `Could not determine the ${key} artifact (a location probe failed); adherence not verified.`, evidence: art.searched ? [{ label: 'searched', detail: art.searched.join(', ') }] : [] }))
      // No spec/plan associated with this PR ⇒ adherence is not applicable (you cannot verify code
      // against a non-existent artifact). Skipped, not failed: absence is already surfaced by the
      // advisory spec-present/plan-present checks, so it must not double-count as a blocking failure.
      else aiAbsent.push(mk(c, { status: 'skipped', summary: `No ${key} artifact associated with this PR; adherence not applicable.`, evidence: art && art.searched ? [{ label: 'searched', detail: art.searched.join(', ') }] : [], remediation: `Associate a ${key} (a PR section, or a file committed in this PR) to have adherence verified.` }))
    } else aiChecks.push(c.id)
  }
  return { checklist, deterministic, todo, aiChecks, aiAbsent, artifacts }
}

// CLI: node deterministic-checks.js pr.json pr.diff outDir   (used by the workflow `steps:`)
if (require.main === module) {
  const fs = require('fs'); const { execFileSync } = require('child_process')
  const [prPath, diffPath, outDir = '/tmp/gh-aw/agent'] = process.argv.slice(2)
  const pr = JSON.parse(fs.readFileSync(prPath, 'utf8'))
  const repo = process.env.REPO, head = process.env.HEAD_SHA || pr.headRefName || pr.headSha
  const gh = (args) => { try { return execFileSync('gh', args, { encoding: 'utf8' }) } catch { return null } }
  const io = {
    probe: async (dir) => { const out = gh(['api', `repos/${repo}/contents/${dir}?ref=${head}`]); if (out == null) return null; try { const j = JSON.parse(out); return Array.isArray(j) ? j.map(e => ({ path: e.path, type: e.type })) : [{ path: j.path, type: j.type }] } catch { return null } },
    readFile: async (p) => { const out = gh(['api', `repos/${repo}/contents/${p}?ref=${head}`, '--jq', '.content']); if (!out) return null; try { return Buffer.from(out.trim(), 'base64').toString('utf8') } catch { return null } },
  }
  ;(async () => {
    const r = await runDeterministic(pr, fs.readFileSync(diffPath, 'utf8'), io)
    fs.writeFileSync(`${outDir}/spec.txt`, r.artifacts.spec.text || '')
    fs.writeFileSync(`${outDir}/plan.txt`, r.artifacts.plan.text || '')
    fs.writeFileSync(`${outDir}/ai-checks.json`, JSON.stringify(r.aiChecks))
    process.stdout.write(JSON.stringify(r))
  })()
}

module.exports = { runDeterministic }

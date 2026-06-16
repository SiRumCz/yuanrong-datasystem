'use strict'
// Locate the committed Claude transcript for a SPECIFIC PR. Mirrors preflight/locate.js:
// a pure, DI-tested core plus a gh-api-backed CLI. Fail-loud: distinguishes an errored
// probe from a clean absence (never infers "missing").
//
// Layout: .conversations/<owner>/<repo>/pr-<number>/<session>.jsonl  (per-PR, so the pipeline
// associates a transcript with the right PR instead of grabbing any transcript in the repo).
const TRANSCRIPT_DIR = '.conversations'

// The per-PR transcript directory for repo `<owner>/<name>` and PR `<number>`.
function prTranscriptDir(repo, number) { return `${TRANSCRIPT_DIR}/${repo}/pr-${number}` }

// A changed path counts only when it's a .jsonl directly inside THIS PR's dir — a different
// PR's transcript (or a nested path) is not this PR's.
function classifyTranscriptPaths(changedPaths, prDir) {
  const re = new RegExp(`(^|/)${prDir.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/[^/]+\\.jsonl$`)
  return (changedPaths || []).filter((p) => re.test(p))
}

// io seam: probe(dir) -> string[] entry paths (throws on error); readFile(path) -> string|null
async function locateTranscript({ changedPaths, prDir } = {}, { probe, readFile } = {}) {
  const searched = [prDir]
  for (const path of classifyTranscriptPaths(changedPaths, prDir)) {
    let text
    try { text = await readFile(path) } catch { return { found: false, error: true, searched } }
    if (text != null) return { found: true, path, text, evidence: ['changed files'] }
  }
  let entries
  try { entries = await probe(prDir) } catch { return { found: false, error: true, searched } }
  for (const path of (entries || []).filter((p) => p.endsWith('.jsonl'))) {
    let text
    try { text = await readFile(path) } catch { return { found: false, error: true, searched } }
    if (text != null) return { found: true, path, text, evidence: ['repo probe'] }
  }
  return { found: false, searched }
}

module.exports = { classifyTranscriptPaths, locateTranscript, prTranscriptDir, TRANSCRIPT_DIR }

// ---- CLI: REPO=<owner/name> node locate.js <pr.json> <out-transcript.jsonl> ----
if (require.main === module) {
  const fs = require('fs')
  const { execFileSync } = require('child_process')
  const [prPath, outPath] = process.argv.slice(2)
  const repo = process.env.REPO
  const pr = JSON.parse(fs.readFileSync(prPath, 'utf8'))
  const ref = pr.headRefOid || pr.headRefName
  const prDir = prTranscriptDir(repo, pr.number)
  const gh = (args) => execFileSync('gh', args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
  const isNotFound = (err) => /Not Found|HTTP 404/i.test(String(err.message || err))
  // List the PR dir (one level); the session .jsonl files live directly inside it.
  const probe = async (dir) => {
    let arr
    try { arr = JSON.parse(gh(['api', `repos/${repo}/contents/${dir}?ref=${ref}`])) }
    catch (err) { if (isNotFound(err)) return []; throw err }
    return arr.filter((e) => e.type === 'file').map((e) => e.path)
  }
  const readFile = async (path) => {
    try { return Buffer.from(gh(['api', `repos/${repo}/contents/${path}?ref=${ref}`, '--jq', '.content']).trim(), 'base64').toString('utf8') }
    catch (err) { if (isNotFound(err)) return null; throw err }
  }
  const changedPaths = (pr.files || []).map((f) => f.filename || f.path).filter(Boolean)
  locateTranscript({ changedPaths, prDir }, { probe, readFile }).then((r) => {
    if (r.found && r.text != null) { fs.writeFileSync(outPath, r.text); process.stderr.write(`locate: wrote ${r.path}\n`) }
    else process.stderr.write(`locate: no transcript (searched ${(r.searched || []).join(', ')}${r.error ? '; probe ERRORED' : ''})\n`)
  }).catch((e) => process.stderr.write(`locate: ${e.message}\n`))
}

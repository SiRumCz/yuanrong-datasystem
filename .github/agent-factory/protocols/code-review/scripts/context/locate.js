'use strict'
// Locate the committed Claude transcript(s) for a SPECIFIC PR from the PR's OWN
// `.conversations/` directory — the transcript is committed into the PR and read at the
// PR head commit. Pure, DI-tested core + a gh-api-backed CLI. Fail-loud: distinguishes an
// errored probe/read from a clean absence (never infers "missing").
//
// Layout (in the PR head tree): .conversations/<session>.jsonl  (flat; one PR may commit
// several session files). locate returns ALL of them, ordered chronologically (earliest
// record first). A PR with no `.conversations/` simply yields no transcript (clean absence).
const CONVERSATIONS_DIR = process.env.CONVERSATIONS_DIR || '.conversations'

// Smallest record timestamp in a transcript (for ordering sessions). Infinity when none,
// so untimestamped sessions sort last but deterministically (the caller tie-breaks by path).
function earliestTimestamp(text) {
  let min = Infinity
  for (const line of String(text).split('\n')) {
    const t = line.trim(); if (!t) continue
    let obj; try { obj = JSON.parse(t) } catch { continue }
    const ts = obj && obj.timestamp ? Date.parse(obj.timestamp) : NaN
    if (!Number.isNaN(ts) && ts < min) min = ts
  }
  return min
}

// io seam: probe(dir) -> string[] entry paths (throws on error); readFile(path) -> string|null
async function locateTranscripts({ prDir } = {}, { probe, readFile } = {}) {
  const searched = [prDir]
  let entries
  try { entries = await probe(prDir) } catch { return { found: false, error: true, searched } }
  const paths = (entries || []).filter((p) => p.endsWith('.jsonl'))
  const sessions = []
  for (const path of paths) {
    let text
    try { text = await readFile(path) } catch { return { found: false, error: true, searched } }
    if (text != null) sessions.push({ path, text, ts: earliestTimestamp(text) })
  }
  if (!sessions.length) return { found: false, searched }
  sessions.sort((a, b) => (a.ts - b.ts) || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0))
  return { found: true, sessions: sessions.map(({ path, text }) => ({ path, text })), evidence: [`${sessions.length} session(s)`], searched }
}

module.exports = { locateTranscripts, earliestTimestamp, CONVERSATIONS_DIR }

// ---- CLI: REPO=<owner/name> node locate.js <pr.json> <out-dir> ----
if (require.main === module) {
  const fs = require('fs')
  const { execFileSync } = require('child_process')
  const [prPath, outDir] = process.argv.slice(2)
  const repo = process.env.REPO
  const pr = JSON.parse(fs.readFileSync(prPath, 'utf8'))
  // The transcript lives IN the PR, so read its `.conversations/` at the PR head commit
  // (headRefOid is commit-pinned; fall back to the branch name). pr.json comes from
  // `gh pr view --json …,headRefName,headRefOid` in the workflow's prefetch step.
  const ref = pr.headRefOid || pr.headRefName
  const dir = CONVERSATIONS_DIR
  const gh = (args) => execFileSync('gh', args, { encoding: 'utf8', maxBuffer: 128 * 1024 * 1024 })
  const isNotFound = (err) => /Not Found|HTTP 404/i.test(String(err.message || err))
  // List the PR's `.conversations/` (one level); session .jsonl files live directly inside it.
  const probe = async (d) => {
    let arr
    try { arr = JSON.parse(gh(['api', `repos/${repo}/contents/${d}?ref=${ref}`])) }
    catch (err) { if (isNotFound(err)) return []; throw err }
    return arr.filter((e) => e.type === 'file').map((e) => e.path)
  }
  // Raw media type: returns the file bytes directly — required for files >1MB, where the
  // Contents API leaves `.content` empty (the previous base64 path silently failed on those).
  const readFile = async (path) => {
    try { return gh(['api', '-H', 'Accept: application/vnd.github.v3.raw', `repos/${repo}/contents/${path}?ref=${ref}`]) }
    catch (err) { if (isNotFound(err)) return null; throw err }
  }
  locateTranscripts({ prDir: dir }, { probe, readFile }).then((r) => {
    if (r.found && r.sessions.length) {
      fs.mkdirSync(outDir, { recursive: true })
      r.sessions.forEach((s, i) => fs.writeFileSync(`${outDir}/${String(i).padStart(3, '0')}.jsonl`, s.text))
      process.stderr.write(`locate: wrote ${r.sessions.length} session(s) to ${outDir}\n`)
    } else process.stderr.write(`locate: no transcript (searched ${(r.searched || []).join(', ')}${r.error ? '; probe ERRORED' : ''})\n`)
  }).catch((e) => process.stderr.write(`locate: ${e.message}\n`))
}

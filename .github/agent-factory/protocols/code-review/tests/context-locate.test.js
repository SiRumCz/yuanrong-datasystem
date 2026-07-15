const { test } = require('node:test')
const assert = require('node:assert/strict')
const { locateTranscripts, earliestTimestamp } = require('../scripts/context/locate.js')

// locate.js now sources transcripts from the dedicated `conversations` branch at
// <owner>/<repo>/pr-<N>/ and no longer exports a PR_DIR default (the CLI
// derives the dir from REPO + pr.number). These DI tests drive the pure core with a
// representative prDir.
const PR_DIR = 'o/r/pr-5'

test('earliestTimestamp returns the smallest record timestamp; Infinity when none', () => {
  const text = '{"timestamp":"2026-06-02T00:00:00Z"}\n{"timestamp":"2026-06-01T00:00:00Z"}\nnot-json'
  assert.equal(earliestTimestamp(text), Date.parse('2026-06-01T00:00:00Z'))
  assert.equal(earliestTimestamp('{"no":"ts"}'), Infinity)
})

test('locateTranscripts: single session via conversations-branch probe', async () => {
  const prDir = PR_DIR
  const probe = async (dir) => dir === prDir ? [`${prDir}/y.jsonl`] : []
  const readFile = async (p) => p === `${prDir}/y.jsonl` ? '{"type":"assistant","timestamp":"2026-06-01T00:00:00Z"}' : null
  const r = await locateTranscripts({ prDir }, { probe, readFile })
  assert.equal(r.found, true)
  assert.equal(r.sessions.length, 1)
  assert.equal(r.sessions[0].path, `${prDir}/y.jsonl`)
})

test('locateTranscripts: multiple sessions returned ordered by earliest timestamp', async () => {
  const prDir = PR_DIR
  const files = {
    [`${prDir}/late.jsonl`]: '{"timestamp":"2026-06-03T00:00:00Z"}',
    [`${prDir}/early.jsonl`]: '{"timestamp":"2026-06-01T00:00:00Z"}',
    [`${prDir}/mid.jsonl`]: '{"timestamp":"2026-06-02T00:00:00Z"}',
    [`${prDir}/notes.txt`]: 'ignore me',
  }
  const probe = async () => Object.keys(files)
  const readFile = async (p) => files[p] ?? null
  const r = await locateTranscripts({ prDir }, { probe, readFile })
  assert.equal(r.found, true)
  assert.deepEqual(r.sessions.map((s) => s.path), [
    `${prDir}/early.jsonl`, `${prDir}/mid.jsonl`, `${prDir}/late.jsonl`,
  ])
})

test('locateTranscripts: clean absence is not an error', async () => {
  const r = await locateTranscripts({ prDir: PR_DIR }, { probe: async () => [], readFile: async () => null })
  assert.equal(r.found, false)
  assert.equal(r.error, undefined)
  assert.ok(r.searched.length)
})

test('locateTranscripts: probe or read errors are explicit', async () => {
  const probeError = await locateTranscripts(
    { prDir: PR_DIR },
    { probe: async () => { throw new Error('boom') }, readFile: async () => null }
  )
  assert.equal(probeError.found, false)
  assert.equal(probeError.error, true)

  const readError = await locateTranscripts(
    { prDir: PR_DIR },
    { probe: async () => [`${PR_DIR}/a.jsonl`], readFile: async () => { throw new Error('io') } }
  )
  assert.equal(readError.found, false)
  assert.equal(readError.error, true)
})

test('locateTranscripts: equal timestamps tie-break alphabetically by path', async () => {
  const prDir = PR_DIR
  const probe = async () => [`${prDir}/z.jsonl`, `${prDir}/a.jsonl`]
  const readFile = async () => '{"timestamp":"2026-06-01T00:00:00Z"}'
  const r = await locateTranscripts({ prDir }, { probe, readFile })
  assert.deepEqual(r.sessions.map((s) => s.path), [`${prDir}/a.jsonl`, `${prDir}/z.jsonl`])
})

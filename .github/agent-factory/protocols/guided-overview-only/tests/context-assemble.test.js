const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')
const { build } = require('../scripts/context/assemble.js')

const fx = (f) => path.join(__dirname, 'fixtures', 'context', f)
const parts = require('./fixtures/context/parts.json')
const phases = fs.readFileSync(fx('phases.jsonl'), 'utf8').split('\n').filter(Boolean).map(JSON.parse)
const pr = require('./fixtures/context/pr.json')

test('build: valid SessionExport with per-phase componentTokens + meta', () => {
  const exp = build(parts, phases, pr)
  assert.equal(exp.version, '1.0')
  assert.equal(exp.files.length, 1)
  assert.equal(exp.groups.length, 0)
  assert.equal(exp.files[0].summary, null)
  assert.ok('analysis' in exp.files[0])
  assert.equal(exp.files[0].analysis, null)
  assert.equal(exp.files[0].metadata.parserName, 'Context Viewer')
  assert.equal(exp.files[0].conversation.messages[0].parts[0].component, 'UNDERSTAND')

  const cc = exp.analytics.componentComparison[0]
  assert.equal(cc.fileId, exp.files[0].id)
  assert.equal(cc.totalTokens, 42)
  assert.equal(cc.componentTokens.IMPLEMENT, 7)
  assert.equal(cc.turnCount, 1)
  assert.equal(cc.messageCount, 2)
  for (const ph of Object.keys(cc.componentTokens)) assert.ok(exp.files[0].colors[ph])
  assert.deepEqual(exp.meta, { pr_number: 42, head_sha: 'abc1234' })
})

test('build: missing parts emits parseable error export with meta', () => {
  const exp = build(null, [], pr)
  assert.ok(exp.error)
  assert.deepEqual(exp.meta, { pr_number: 42, head_sha: 'abc1234' })
  assert.deepEqual(exp.analytics.componentComparison, [])
})

test('CLI: missing inputs still emits parseable error export', () => {
  const scriptPath = require.resolve('../scripts/context/assemble.js')
  const out = execFileSync('node', [scriptPath, '/no/parts.json', '/no/phases.jsonl', fx('pr.json')], { encoding: 'utf8' })
  const exp = JSON.parse(out)
  assert.ok(exp.error)
  assert.deepEqual(exp.meta, { pr_number: 42, head_sha: 'abc1234' })
})

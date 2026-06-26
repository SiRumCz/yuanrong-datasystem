// CLI: node run-cedar.js <defaultDir> <customDir-or-empty> <convDir> <changedPathsJson>
// Reads *.jsonl from convDir, merges Cedar policies, runs analyzeTranscripts, prints JSON.
'use strict'
const fs = require('node:fs'); const path = require('node:path')
const { mergeCedar } = require('./policy-merge.js')
const { analyzeTranscripts } = require('./actions-from-transcript.js')

const [,, defaultDir, customDir, convDir, changedPathsJson] = process.argv
const texts = fs.existsSync(convDir)
  ? fs.readdirSync(convDir).filter(f => f.endsWith('.jsonl'))
      .map(f => fs.readFileSync(path.join(convDir, f), 'utf8'))
  : []
const { policiesText } = mergeCedar(defaultDir, customDir || null)
const changedPaths = JSON.parse(changedPathsJson || '[]')
console.log(JSON.stringify(analyzeTranscripts(texts, { policiesText, allowedHosts: [], changedPaths })))

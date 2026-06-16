#!/usr/bin/env bun
// Claude .jsonl transcript -> context-viewer parts.json (messages[].parts[] with token_count)
// + meta (model + real Claude usage). Vendored subset under ./cv. Run: bun driver.ts <in.jsonl> <out.json>
import { readFile, writeFile } from 'fs/promises'
import { ClaudeTranscriptsParser } from './cv/parsers/claude-transcripts-parser'
import { addTokenCounts } from './cv/add-token-counts'

const inPath = process.argv[2]
const outPath = process.argv[3] || 'parts.json'
const raw = await readFile(inPath, 'utf-8')
const entries = raw.split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l))

const parser = new ClaudeTranscriptsParser()
const conversation = parser.parse(entries)
const meta = parser.extractMetadata(entries)        // { model, provider }
const withCounts = await addTokenCounts(conversation)

// The parser DROPS real Claude usage; scan raw entries, dedupe by message.id (streaming repeats it).
let input_tokens = 0, output_tokens = 0, cache_creation_input_tokens = 0, cache_read_input_tokens = 0
const seen = new Set<string>()
for (const e of entries) {
  if (e && e.type === 'assistant' && e.message && e.message.usage) {
    const id = e.message.id
    if (id && seen.has(id)) continue
    if (id) seen.add(id)
    const u = e.message.usage
    input_tokens += u.input_tokens ?? 0
    output_tokens += u.output_tokens ?? 0
    cache_creation_input_tokens += u.cache_creation_input_tokens ?? 0
    cache_read_input_tokens += u.cache_read_input_tokens ?? 0
  }
}

await writeFile(outPath, JSON.stringify({
  meta: { ...meta, realUsage: { input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens } },
  messages: withCounts.messages,
}, null, 2))
process.stderr.write(`parts: ${withCounts.messages.length} messages -> ${outPath}\n`)

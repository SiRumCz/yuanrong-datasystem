#!/usr/bin/env bun
// Claude .jsonl transcript(s) -> context-viewer parts.json (messages[].parts[] with token_count)
// + meta (model + real Claude usage). Accepts a single .jsonl file OR a directory of session
// files; sessions are parsed independently (preserving each session's parentUuid tree) then
// merged. Vendored subset under ./cv. Run: bun driver.ts <in-file-or-dir> <out.json>
import { readFile, writeFile, readdir, stat } from 'fs/promises'
import { ClaudeTranscriptsParser } from './cv/parsers/claude-transcripts-parser'
import { addTokenCounts } from './cv/add-token-counts'
import type { Message } from './cv/schema'

const inPath = process.argv[2]
const outPath = process.argv[3] || 'parts.json'

// Resolve the ordered list of session files: a directory yields its *.jsonl sorted by name
// (locate.js writes 000.jsonl, 001.jsonl, … in chronological order); a file yields itself.
const st = await stat(inPath)
const files = st.isDirectory()
  ? (await readdir(inPath)).filter((f) => f.endsWith('.jsonl')).sort().map((f) => `${inPath}/${f}`)
  : [inPath]

const parser = new ClaudeTranscriptsParser()

if (files.length === 0) {
  await writeFile(outPath, JSON.stringify({ meta: { realUsage: { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 } }, messages: [] }, null, 2))
  process.stderr.write(`parts: no .jsonl sessions in ${inPath} -> wrote empty ${outPath}\n`)
  process.exit(0)
}

const mergedMessages: Message[] = []
let model: string | undefined
let provider: string | undefined

// Real Claude usage, summed across every session; dedupe by message.id (streaming repeats it,
// and ids are unique across sessions so the shared Set is safe).
let input_tokens = 0, output_tokens = 0, cache_creation_input_tokens = 0, cache_read_input_tokens = 0
const seen = new Set<string>()

for (const file of files) {
  const raw = await readFile(file, 'utf-8')
  const entries = raw.split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l))
  const conversation = parser.parse(entries)
  const withCounts = await addTokenCounts(conversation)
  for (const m of withCounts.messages) mergedMessages.push(m)
  if (!model) {
    const meta = parser.extractMetadata(entries)
    if (meta.model) { model = meta.model; provider = meta.provider }
  }
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
}

await writeFile(outPath, JSON.stringify({
  meta: { model, provider, realUsage: { input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens } },
  messages: mergedMessages,
}, null, 2))
process.stderr.write(`parts: ${mergedMessages.length} messages from ${files.length} session(s) -> ${outPath}\n`)

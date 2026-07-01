// app/backend/component/reviewers/workflow/security/actions-from-transcript.js
'use strict'
const { decide } = require('./_cedar-decide.js')
// Pure tool_use classification is shared with Guardians' transcript-extract.js (which must NOT pull
// in the Cedar wasm engine required above), so it lives in a dependency-free module.
const { toAction, toolUses } = require('./_transcript-actions.js')

// analyzeTranscripts(jsonlTexts[], { policiesText, allowedHosts, changedPaths }) -> { status, flags }
function analyzeTranscripts(jsonlTexts, { policiesText, allowedHosts = [], changedPaths = [] }) {
  if (!jsonlTexts || !jsonlTexts.length) return { status: 'n/a', flags: [] }
  let touchedSecret = false  // sticky across the session: once a secret is read, egress is exfil
  const flags = []
  const lockedIds = new Set(['locked.no-exfiltration', 'locked.no-destructive'])
  for (const text of jsonlTexts) {
    for (const { name, input } of toolUses(text)) {
      const a = toAction(name, input); if (!a) continue
      if (a.touched_secret) touchedSecret = true
      const context = {
        touched_secret: touchedSecret,
        external_host: !!a.external_host,
        destructive: !!a.destructive,
        in_changed_set: a.resourceType === 'File' ? changedPaths.includes(a.resource) : true,
        in_repo: a.resourceType === 'File',
        allowed_hosts: allowedHosts,
        // Task 3 fix: net.egress-allowlist permit gates on context.allowed_hosts.contains(context.host)
        // so host must be present for every request; non-Network actions get ''.
        host: a.action === 'Network' ? a.resource : '',
      }
      const request = {
        principal: 'Agent::"session"',
        action: `Action::"${a.action}"`,
        resource: `${a.resourceType}::"${a.resource}"`,
        context,
      }
      if (decide(policiesText, '[]', request) === 'Deny') {
        // Attribute the deny to the most security-relevant rule deterministically from the action shape.
        const determining_id =
          (context.touched_secret && context.external_host) ? 'locked.no-exfiltration' :
          a.destructive ? 'locked.no-destructive' :
          a.action === 'WriteFile' ? 'scope.writes-in-changed-set' :
          a.action === 'Network' ? 'net.egress-allowlist' : 'unknown'
        flags.push({ tool: name, action: a.action, resource: a.resource, context,
                     determining_id, locked: lockedIds.has(determining_id) })
      }
    }
  }
  return { status: 'ok', flags }
}

module.exports = { analyzeTranscripts, toAction, toolUses }

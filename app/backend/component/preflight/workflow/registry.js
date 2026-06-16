// The preflight gate's check registry: pure metadata describing every check the gate
// knows about, including ones not yet implemented (state: 'todo'). server.js runs the
// deterministic checks (checks.js), feeds the AI checks to prompt.js, and emits a 'todo'
// result for todo checks so they are always visible (CLAUDE.md Rule 12).

const CHECKS = [
  { id: 'spec-present',            name: 'Spec / requirements artifact present', category: 'documentation', kind: 'deterministic', severity: 'blocker', state: 'implemented' },
  { id: 'plan-present',            name: 'Implementation plan artifact present', category: 'documentation', kind: 'deterministic', severity: 'blocker', state: 'implemented' },
  { id: 'spec-adherence',          name: 'Code adheres to the spec',             category: 'process',       kind: 'ai',            severity: 'blocker', state: 'implemented' },
  { id: 'plan-adherence',          name: 'Code follows the implementation plan', category: 'process',       kind: 'ai',            severity: 'blocker', state: 'implemented' },
  { id: 'docs-updated-with-code',  name: 'Docs updated alongside code',          category: 'coherence',     kind: 'deterministic', severity: 'warn',    state: 'implemented' },
  { id: 'tests-updated-with-code', name: 'Tests updated alongside code',         category: 'coherence',     kind: 'deterministic', severity: 'warn',    state: 'implemented' },
  { id: 'local-review-evidence',   name: 'Local review done before push',        category: 'review',        kind: 'ai',            severity: 'info',    state: 'todo' },
]

// Which gathered artifact an AI adherence check verifies against. When that artifact is not
// associated with the PR, the adherence check is skipped (not inferred, not failed) instead of
// calling the AI — absence is surfaced separately by the advisory spec-present/plan-present checks.
const AI_CHECK_ARTIFACT = {
  'spec-adherence': 'spec',
  'plan-adherence': 'plan',
}

// Deterministic roll-up: BLOCKED if any blocker check failed or errored; else CLEAR.
// warn/info never block; todo never blocks (it is unbuilt) but is counted.
function computeVerdict(results) {
  const counts = { pass: 0, fail: 0, warn: 0, todo: 0, error: 0, skipped: 0 }
  let blocked = false
  for (const r of results) {
    if (counts[r.status] !== undefined) counts[r.status]++
    if (r.severity === 'blocker' && (r.status === 'fail' || r.status === 'error')) blocked = true
  }
  return { type: 'verdict', status: blocked ? 'blocked' : 'clear', counts }
}

module.exports = { CHECKS, AI_CHECK_ARTIFACT, computeVerdict }

// Pure deterministic gate checks. Each takes the gathered context and returns a partial
// CheckResult { status, summary, evidence?, remediation? }. No I/O — operates on context
// produced by gather.js, so each is trivially unit-testable (CLAUDE.md Rule 5).

// File classification by path. A test file is never also counted as code.
function isDocFile(p)  { return /\.(md|mdx|rst|adoc|txt)$/i.test(p) || /(^|\/)docs?\//i.test(p) }
function isTestFile(p) { return /(\.|_)(test|spec)\.[a-z0-9]+$/i.test(p) || /(^|\/)(tests?|__tests__|spec)\//i.test(p) }
function isCodeFile(p) { return !isDocFile(p) && !isTestFile(p) && /\.[a-z0-9]+$/i.test(p) }

function changedPaths(ctx) {
  return (ctx.changedFiles || []).filter(f => f.status !== 'removed').map(f => f.filename)
}

function specPresent(ctx) {
  const a = (ctx.artifacts && ctx.artifacts.spec) || {}
  if (a.found) return { status: 'pass', summary: 'A spec/requirements artifact was located.', evidence: a.evidence || [] }
  if (a.error) return {
    status: 'error',
    summary: 'Could not determine spec/requirements presence — an artifact-location probe failed. Not treated as absent.',
    evidence: a.searched ? [{ label: 'searched', detail: a.searched.join(', ') }] : []
  }
  return {
    status: 'warn',
    summary: 'No spec/requirements artifact associated with this PR (none in its diff or description). Advisory only — adherence is not checked without one.',
    evidence: a.searched ? [{ label: 'searched', detail: a.searched.join(', ') }] : [],
    remediation: 'Add a "## Requirements" section to the PR description, or commit a spec under docs/specs/ in this PR.'
  }
}

function planPresent(ctx) {
  const a = (ctx.artifacts && ctx.artifacts.plan) || {}
  if (a.found) return { status: 'pass', summary: 'An implementation-plan artifact was located.', evidence: a.evidence || [] }
  if (a.error) return {
    status: 'error',
    summary: 'Could not determine implementation-plan presence — an artifact-location probe failed. Not treated as absent.',
    evidence: a.searched ? [{ label: 'searched', detail: a.searched.join(', ') }] : []
  }
  return {
    status: 'warn',
    summary: 'No implementation-plan artifact associated with this PR (none in its diff or description). Advisory only — adherence is not checked without one.',
    evidence: a.searched ? [{ label: 'searched', detail: a.searched.join(', ') }] : [],
    remediation: 'Add a task checklist / "## Plan" to the PR description, or commit a plan under docs/**/plans/ in this PR.'
  }
}

function docsUpdatedWithCode(ctx) {
  const paths = changedPaths(ctx)
  const code = paths.filter(isCodeFile)
  const docs = paths.filter(isDocFile)
  if (code.length === 0) return { status: 'pass', summary: 'No code files changed; doc-coherence not applicable.' }
  if (docs.length > 0)  return { status: 'pass', summary: `Docs updated alongside code (${docs.length} doc file(s)).`, evidence: docs.slice(0, 10).map(d => ({ label: 'doc', detail: d })) }
  return {
    status: 'warn',
    summary: `Code changed (${code.length} file(s)) but no documentation was updated.`,
    evidence: code.slice(0, 10).map(c => ({ label: 'code', detail: c })),
    remediation: 'Update README/docs to reflect the change, or confirm none is needed.'
  }
}

function testsUpdatedWithCode(ctx) {
  const paths = changedPaths(ctx)
  const code = paths.filter(isCodeFile)
  const tests = paths.filter(isTestFile)
  if (code.length === 0) return { status: 'pass', summary: 'No code files changed; test-coherence not applicable.' }
  if (tests.length > 0)  return { status: 'pass', summary: `Tests updated alongside code (${tests.length} test file(s)).`, evidence: tests.slice(0, 10).map(t => ({ label: 'test', detail: t })) }
  return {
    status: 'warn',
    summary: `Code changed (${code.length} file(s)) but no tests were added or updated.`,
    evidence: code.slice(0, 10).map(c => ({ label: 'code', detail: c })),
    remediation: 'Add or update tests covering the change, or confirm none are needed.'
  }
}

const DETERMINISTIC = {
  'spec-present': specPresent,
  'plan-present': planPresent,
  'docs-updated-with-code': docsUpdatedWithCode,
  'tests-updated-with-code': testsUpdatedWithCode,
}

module.exports = {
  isDocFile, isTestFile, isCodeFile,
  specPresent, planPresent, docsUpdatedWithCode, testsUpdatedWithCode, DETERMINISTIC
}

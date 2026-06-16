// Published-model risk scoring — we do NOT invent a point system. The change's intrinsic risk
// uses Mockus & Weiss (2000), "Predicting risk of software changes": a logistic model over
// log-transformed change-diffusion + size with their published coefficients (NS 0.41, ND 0.10,
// LA 0.18). Breaking-change severity is the PRIMARY, centering factor (Dig & Johnson 2006
// recoverable-vs-hard axis) — a change with no breaking change has zero breaking-change risk.
// Per Ochoa et al. 2022 we modulate severity by blast radius, operationalized intra-repo as the
// number of modified subsystems (Kamei NS). Taxonomy = APIDiff (Brito et al.). No I/O. See
// docs/superpowers/specs/2026-06-10-risk-component-research-grounding.md.
const { computeDiffusion, computeChurn } = require('../../core/diffusion.js')

// Mockus & Weiss (2000) coefficients on log-transformed predictors. We leverage the validated
// RELATIVE coefficients + model form; `ref` is a display calibration (their absolute intercept
// is dataset-specific, not transferable).
const MW = { NS: 0.41, ND: 0.10, LA: 0.18, ref: 0.6 }
// Dig & Johnson (2006): hard breaks weighted higher than recoverable refactorings.
const SEVERITY_WEIGHT = { 'hard-break': 1.0, 'recoverable-refactor': 0.3 }
// Ochoa (2022) blast radius via Kamei's NS: a hard break spanning ≥2 subsystems → Critical.
const WIDE_NS = 2
const BAND_ORDER = ['Low', 'Medium', 'High', 'Critical']

function sigmoid(x) { return 1 / (1 + Math.exp(-x)) }
function ln1p(x) { return Math.log(1 + x) }
function round2(x) { return Math.round(x * 100) / 100 }
function round4(x) { return Math.round(x * 10000) / 10000 }

// Mockus & Weiss change-risk probability over diffusion (NS, ND) + size (LA).
function changeRisk(diffusion, LA) {
  const lp = MW.NS * ln1p(diffusion.NS) + MW.ND * ln1p(diffusion.ND) + MW.LA * ln1p(LA)
  return round4(sigmoid(lp - MW.ref))
}

function cohortFiles(cohort, fileStats) {
  return (cohort.files || []).map(fn => ({
    filename: fn,
    additions: (fileStats[fn] || {}).additions || 0,
    deletions: (fileStats[fn] || {}).deletions || 0,
  }))
}

function scoreCohort(cohort, fileStats) {
  const findings = cohort.bcFindings || []
  const hard = findings.filter(f => f.severityClass === 'hard-break').length
  const recoverable = findings.filter(f => f.severityClass === 'recoverable-refactor').length
  const files = cohortFiles(cohort, fileStats || {})
  const diffusion = computeDiffusion(files)
  const churn = computeChurn(files)                       // normalized, reported (Nagappan & Ball)
  const LA = files.reduce((s, f) => s + (f.additions || 0), 0)
  const P = changeRisk(diffusion, LA)                     // Mockus & Weiss change-risk probability

  // Severity = the dominant breaking change present (Dig & Johnson). 0 ⇒ no breaking-change risk.
  const bcSeverity = hard > 0 ? SEVERITY_WEIGHT['hard-break'] : recoverable > 0 ? SEVERITY_WEIGHT['recoverable-refactor'] : 0

  // Band centered on breaking changes; Critical escalation is blast-radius-driven (Ochoa / Kamei NS).
  let band
  if (hard > 0) band = diffusion.NS >= WIDE_NS ? 'Critical' : 'High'
  else if (recoverable > 0) band = 'Medium'
  else band = 'Low'

  // Score = breaking-change severity modulated by the change-risk probability. No BC ⇒ score 0
  // (the score IS breaking-change risk).
  const score = round2(bcSeverity * (0.5 + 0.5 * P))

  return {
    cohort: cohort.cohort || '',
    cohortOrder: cohort.cohortOrder || 0,
    area: cohort.area || '',
    band, score,
    bcFindings: findings,
    diffusion, churn, changeRisk: P,
    files: cohort.files || [],
  }
}

function score(findings, fileStats) {
  const cohorts = (findings || []).map(c => scoreCohort(c, fileStats || {})).sort((a, b) => (a.cohortOrder || 0) - (b.cohortOrder || 0))
  const counts = { Critical: 0, High: 0, Medium: 0, Low: 0 }
  let overallIdx = 0, overallScore = 0
  for (const c of cohorts) {
    if (counts[c.band] !== undefined) counts[c.band]++
    overallIdx = Math.max(overallIdx, BAND_ORDER.indexOf(c.band))
    overallScore = Math.max(overallScore, c.score)
  }
  // overall.band and overall.score are independent maxima: band = the worst cohort's
  // severity+blast-radius category; score = the highest continuous severity×change-risk. They
  // may come from different cohorts — overall.score is not a refinement of overall.band.
  const overall = { band: cohorts.length ? BAND_ORDER[overallIdx] : 'Low', score: round2(overallScore), counts }
  return { overall, cohorts }
}

module.exports = { score, scoreCohort, changeRisk, SEVERITY_WEIGHT, MW, WIDE_NS, BAND_ORDER }

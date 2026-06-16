// Pure JIT change-diffusion + normalized-churn factors over a changed-files array.
// Grounded in Kamei et al. 2013 (NF/ND/NS/entropy) + Hassan 2009 (normalized Shannon
// entropy) + Nagappan & Ball 2005 (relative churn — never raw LA/LD). No I/O, so trivially
// unit-testable. See docs/superpowers/specs/2026-06-10-risk-component-research-grounding.md.
// files: [{ filename, additions, deletions }]

// Root-level files share the '' bucket: the repo root counts as one directory / one subsystem.
function topSubsystem(p) { const seg = String(p).split('/'); return seg.length > 1 ? seg[0] : '' }
function dirOf(p) { const i = String(p).lastIndexOf('/'); return i >= 0 ? String(p).slice(0, i) : '' }
function changedLines(f) { return (f.additions || 0) + (f.deletions || 0) }
function round4(x) { return Math.round(x * 10000) / 10000 }

function computeDiffusion(files) {
  const list = files || []
  const NF = list.length
  const ND = new Set(list.map(f => dirOf(f.filename))).size
  const NS = new Set(list.map(f => topSubsystem(f.filename))).size
  // Normalized Shannon entropy of per-file changed-line proportions (Hassan 2009).
  // Only files with >0 changed lines contribute; n = that count; H/log2(n) ∈ [0,1].
  const active = list.filter(f => changedLines(f) > 0)
  const n = active.length
  let entropy = 0
  if (n > 1) {
    const total = active.reduce((s, f) => s + changedLines(f), 0)
    let h = 0
    for (const f of active) { const p = changedLines(f) / total; h -= p * Math.log2(p) }
    entropy = h / Math.log2(n)
  }
  return { NF, ND, NS, entropy: round4(entropy) }
}

function computeChurn(files) {
  const list = files || []
  const LA = list.reduce((s, f) => s + (f.additions || 0), 0)
  const LD = list.reduce((s, f) => s + (f.deletions || 0), 0)
  const denom = LA + LD
  // Relative churn (Nagappan & Ball). LA/LT (size-normalized) is deferred (needs base sha).
  return denom === 0 ? 0 : round4(LA / denom)
}

module.exports = { computeDiffusion, computeChurn }

#!/usr/bin/env python3
"""Deterministic breaking-change risk scorer — a verbatim Python port of custody's
app/backend/component/risk/score.js + app/backend/core/diffusion.js. Pure, no I/O,
so it is trivially unit-testable and fully reproducible offline.

We do NOT invent a point system. The change's intrinsic risk uses Mockus & Weiss
(2000) — a logistic model over log-transformed change-diffusion + size with their
published coefficients (NS 0.41, ND 0.10, LA 0.18). Breaking-change severity is the
PRIMARY, centering factor (Dig & Johnson 2006 recoverable-vs-hard axis) — a change
with no breaking change has zero breaking-change risk. Per Ochoa et al. 2022 we
modulate severity by blast radius, operationalized intra-repo as the number of
modified subsystems (Kamei NS). Taxonomy = APIDiff (Brito et al.). Churn (Nagappan &
Ball, normalized) and entropy (Hassan 2009) are reported but NOT in the score/band.

The agent (the overview workflow) only partitions cohorts and classifies APIDiff
breaking changes (severityClass); this numeric model is computed downstream, exactly
as in custody. Imported by conclude-overview.py.
"""
import math

# Mockus & Weiss (2000) coefficients on log-transformed predictors. The validated
# RELATIVE coefficients + model form; `ref` is a display calibration (their absolute
# intercept is dataset-specific, not transferable).
MW = {"NS": 0.41, "ND": 0.10, "LA": 0.18, "ref": 0.6}
# Dig & Johnson (2006): hard breaks weighted higher than recoverable refactorings.
SEVERITY_WEIGHT = {"hard-break": 1.0, "recoverable-refactor": 0.3}
# Ochoa (2022) blast radius via Kamei's NS: a hard break spanning >=2 subsystems -> Critical.
WIDE_NS = 2
BAND_ORDER = ["Low", "Medium", "High", "Critical"]


def _js_round(x, places):
    """Match JavaScript Math.round(x*f)/f (round-half-up for the non-negative values
    this scorer produces), NOT Python's banker's rounding, so the port is byte-equal
    to score.js/diffusion.js."""
    f = 10 ** places
    return math.floor(x * f + 0.5) / f


def round2(x):
    return _js_round(x, 2)


def round4(x):
    return _js_round(x, 4)


def _sigmoid(x):
    return 1 / (1 + math.exp(-x))


def _ln1p(x):
    return math.log(1 + x)


# ---- diffusion.js port -------------------------------------------------------

def _top_subsystem(p):
    # Root-level files share the '' bucket: the repo root counts as one subsystem.
    seg = str(p).split("/")
    return seg[0] if len(seg) > 1 else ""


def _dir_of(p):
    p = str(p)
    i = p.rfind("/")
    return p[:i] if i >= 0 else ""


def _changed_lines(f):
    return (f.get("additions") or 0) + (f.get("deletions") or 0)


def compute_diffusion(files):
    """Kamei NF/ND/NS + Hassan normalized Shannon entropy. files: [{filename,additions,deletions}]."""
    lst = files or []
    NF = len(lst)
    ND = len({_dir_of(f.get("filename")) for f in lst})
    NS = len({_top_subsystem(f.get("filename")) for f in lst})
    active = [f for f in lst if _changed_lines(f) > 0]
    n = len(active)
    entropy = 0.0
    if n > 1:
        total = sum(_changed_lines(f) for f in active)
        h = 0.0
        for f in active:
            p = _changed_lines(f) / total
            h -= p * math.log2(p)
        entropy = h / math.log2(n)
    return {"NF": NF, "ND": ND, "NS": NS, "entropy": round4(entropy)}


def compute_churn(files):
    """Relative churn (Nagappan & Ball): LA/(LA+LD); 0 when nothing changed."""
    lst = files or []
    LA = sum((f.get("additions") or 0) for f in lst)
    LD = sum((f.get("deletions") or 0) for f in lst)
    denom = LA + LD
    return 0 if denom == 0 else round4(LA / denom)


# ---- score.js port -----------------------------------------------------------

def change_risk(diffusion, LA):
    """Mockus & Weiss change-risk probability over diffusion (NS, ND) + size (LA)."""
    lp = (MW["NS"] * _ln1p(diffusion.get("NS", 0))
          + MW["ND"] * _ln1p(diffusion.get("ND", 0))
          + MW["LA"] * _ln1p(LA))
    return round4(_sigmoid(lp - MW["ref"]))


def _cohort_files(cohort, file_stats):
    out = []
    for fn in (cohort.get("files") or []):
        st = file_stats.get(fn) or {}
        out.append({"filename": fn,
                    "additions": st.get("additions") or 0,
                    "deletions": st.get("deletions") or 0})
    return out


def score_cohort(cohort, file_stats):
    findings = cohort.get("bcFindings") or []
    hard = sum(1 for f in findings if f.get("severityClass") == "hard-break")
    recoverable = sum(1 for f in findings if f.get("severityClass") == "recoverable-refactor")
    files = _cohort_files(cohort, file_stats or {})
    diffusion = compute_diffusion(files)
    churn = compute_churn(files)
    LA = sum((f.get("additions") or 0) for f in files)
    P = change_risk(diffusion, LA)

    # Severity = the dominant breaking change present (Dig & Johnson). 0 => no BC risk.
    bc_severity = (SEVERITY_WEIGHT["hard-break"] if hard > 0
                   else SEVERITY_WEIGHT["recoverable-refactor"] if recoverable > 0
                   else 0)

    # Band centered on breaking changes; Critical escalation is blast-radius-driven.
    if hard > 0:
        band = "Critical" if diffusion["NS"] >= WIDE_NS else "High"
    elif recoverable > 0:
        band = "Medium"
    else:
        band = "Low"

    # Score = breaking-change severity modulated by the change-risk probability.
    score_val = round2(bc_severity * (0.5 + 0.5 * P))

    return {
        "cohort": cohort.get("cohort") or "",
        "cohortOrder": cohort.get("cohortOrder") or 0,
        "area": cohort.get("area") or "",
        "band": band, "score": score_val,
        "bcFindings": findings,
        "diffusion": diffusion, "churn": churn, "changeRisk": P,
        "files": cohort.get("files") or [],
    }


def score(findings, file_stats):
    """Score every cohort and roll up the overall verdict. Mirrors score.js exactly:
    overall.band = worst cohort band; overall.score = max cohort score; counts tally."""
    cohorts = sorted(
        (score_cohort(c, file_stats or {}) for c in (findings or [])),
        key=lambda c: c.get("cohortOrder") or 0,
    )
    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    overall_idx = 0
    overall_score = 0
    for c in cohorts:
        if c["band"] in counts:
            counts[c["band"]] += 1
        overall_idx = max(overall_idx, BAND_ORDER.index(c["band"]))
        overall_score = max(overall_score, c["score"])
    # overall.band and overall.score are INDEPENDENT maxima and may originate from
    # different cohorts; overall.score is not a refinement of overall.band.
    overall = {
        "band": BAND_ORDER[overall_idx] if cohorts else "Low",
        "score": round2(overall_score),
        "counts": counts,
    }
    return {"overall": overall, "cohorts": cohorts}

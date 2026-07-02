# MRP deterministic scripts — provenance & edit policy

These implement the `mrp` (Merge-Readiness Pack) phase's deterministic post-step:
turn upstream phase evidence + the agent's judgment slices into the custody-shaped
`mrp.json` pack, then derive the engine evidence from it.

## Source

Logic-ported from custody `app/backend/component/mrp/workflow/scripts/` (source tree
state `d79af41`):

| This file | Custody source | Relationship |
|---|---|---|
| `pack_map.py` | `pack-map.js` | **Verbatim logic port** of `buildAcceptancePlan` (`RUNG_FOR_BAND`, `STAGED_RUNGS`, `QUESTION_RUNGS`, per-cohort mapping). Pure, no I/O. |
| `assemble-mrp.py` | `assemble-mrp.js` | **Engine-native re-implementation** of the same `mrp.json` output shape. |
| `to-evidence.py` | *(none)* | Repo-specific glue: `mrp.json` → engine `evidence.json`. |

## Why a Python port instead of vendoring the JS byte-identical

Custody's `assemble-mrp.js` consumes the **conclude/scored** artifacts that custody's
`gather.js` pulls (e.g. `overview.json` with **per-cohort `band`s** already scored,
`verdict.records[]`, `export.summary.totalTokens`). The engine's `inputs[]` mechanism
delivers each phase's **agent evidence** instead, which does *not* carry those derived
fields — most importantly, the overview agent evidence has **no per-cohort band**
(only one top-level `risk_band`).

So a byte-identical `assemble-mrp.js` fed engine inputs would produce a degenerate
pack (all cohorts band-less → all `L0`). Instead, `assemble-mrp.py`:

- reads the engine evidence (`preflight` / `overview` / `triage` / `context`) inlined
  via the engine `inputs[]` (`task-context.json` `.inputs.<phase>`),
- **re-derives per-cohort bands with the engine's own `_risk_score`** (the `score.js`
  port `conclude-overview` already uses — never a second risk model),
- computes spec/plan adherence from the preflight evidence `checks[]`, and total tokens
  from the context evidence `phases[].token_count`,
- then calls the ported `pack_map.build_acceptance_plan` for the keystone
  `acceptance_plan`.

`pack_map.py` is kept a faithful, independently-testable port so the band→rung logic
stays in lockstep with custody's `pack-map.js`.

## Edit policy

- `pack_map.py`: keep in sync with custody `pack-map.js` (port the logic, do not drift
  the rung mapping). Update the source-state reference above on any re-sync.
- `assemble-mrp.py` / `to-evidence.py`: engine glue — evolve with the engine's evidence
  shapes and `_risk_score`. They are NOT byte-parity targets.

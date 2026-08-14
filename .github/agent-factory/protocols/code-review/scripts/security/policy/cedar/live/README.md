# `policy/cedar/live` — universal policy corpus for the live PreToolUse guard

Vendored, **verbatim**, from the upstream Sondera coding-agent hooks corpus.
This directory is the policy set for the **live** call site (`live-guard/`), which
speaks the `Sondera::Action::…` vocabulary. It is separate from
`policy/cedar/default/` (the 7 hand-written policies of the *plan* path, bare
`Action::…`); the two namespaces do not collide and neither directory is loaded by
the other's driver.

## Provenance

| | |
|---|---|
| Upstream | <https://github.com/sondera-ai/sondera-coding-agent-hooks> |
| Commit | `bfa1541024f868177715d65bee82fbc06e14ff83` (2026-08-06) |
| Source path | `.sondera/policies/cedar/` |
| License | MIT, © 2026 Sondera, Inc. — see `LICENSE-sondera` |
| Edits | none; every `.cedar` / `.cedarschema` file here is a byte-for-byte copy |

## Selection rule (mechanical, not hand-picked)

> Take every `*.cedar` in the upstream corpus that references **neither**
> `context.signature` **nor** the bare word `label`.

```bash
python3 - <<'EOF'
import re, glob
sel = [f for f in sorted(glob.glob("*.cedar"))
       if not re.search(r"context\.signature|\blabel\b", open(f).read())]
print(len(sel))   # -> 63
EOF
```

**Counts** (upstream total: 110 `*.cedar` files):

| Bucket | Count | Vendored |
|---|---|---|
| rule-selected (no `context.signature`, no `label`) | **63** | yes |
| excluded — YARA `context.signature` and/or IFC `label` | 47 | no |
| `base.cedar` (inside the 47; see below) | 1 | **yes, deliberately** |
| **files in this directory** | **64** `.cedar` | + `base.cedarschema`, `LICENSE-sondera`, this README |

The 63 are the destructive / delete / docker / kubectl / git-force /
living-off-the-land family. They are **universal** — one set for every agent, no
per-node scoping.

### Why `base.cedar` is here even though the rule excludes it

The rule excludes `base.cedar` only because the word `label` appears in its
**comment header** (the doc block listing each action's context members). Its
executable content is a single statement:

```cedar
@id("default-permit")
permit (principal, action, resource);
```

That is the corpus's **only** `permit`. Cedar is default-deny, so the 63 forbids
*alone* deny every tool call, including `cat`. Vendoring the 63 without
`base.cedar` produces a guard that blocks everything — which is exactly the
failure the negative-control test exists to catch. Applying the same rule to
comment-stripped text selects 64 files, i.e. the 63 plus `base.cedar`; that is the
rule as intended, and `tests/protocols/code-review/test_live_guard_policies.py`
enforces it against this directory.

## Runtime facts

- **The schema is not loaded.** `base.cedarschema` is vendored as the *contract*
  for the live vocabulary (and as input to a future authoring linter). Cedar
  evaluates without it; `live-guard/decide.js` passes `entities: []`.
- **`entities: []` is sound.** No policy in the 64 dereferences an entity
  attribute. The only entity attribute used anywhere in the upstream corpus is
  `resource.label` (9 files), all excluded by the rule.
- **Actions actually constrained by these policies:** `ShellCommand`, `FileRead`,
  `FileWrite`, `FileEdit`, `FileDelete`. Nothing here constrains `WebFetch` or the
  generic `PreToolUse` action, so those requests reach only `default-permit`.
- **Context members these policies read:** `command`, `path_normalized`, and
  `parse.{ok,program_flags,program_args}` (3 policies — `forbid-rm-rf`,
  `forbid-rm-recursive`, `forbid-rm-system-auth-files` — each of which has a
  `parse.ok == false` fallback onto `command like`, so a stub `parse` is a correct
  starting point).
- **Not covered by this corpus:** exfiltration (phase B2) and anything needing a
  real shell parse (phase B3). Notably there is **no `git push --force` policy** in
  the upstream corpus at all — see the note in
  `tests/protocols/code-review/test_live_guard_decide.py`.

## Re-vendoring

Clone the upstream repo, then re-run the rule above and copy the selected files
plus `base.cedar`, `base.cedarschema` and `LICENSE`. Update the commit SHA in this
README and in `LICENSE-sondera`. Do not edit policy text: local rules belong in a
separate directory so the vendored set stays diffable against upstream.

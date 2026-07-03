<!-- Shared skill-invocation preamble, runtime-imported by the design & implement
agents via `{{#runtime-import .github/workflows/shared/skill-preamble.md}}`.
gh-aw runs `claude --print`, where the using-superpowers SessionStart hook may not
fire, so we inline this pressure to make the model reliably reach for the bare-named
PROJECT skills staged under .claude/skills/. Adapted from superpowers' own
using-superpowers SKILL.md. This file has no `on:` field, so gh-aw treats it as a
shared component and does not compile it on its own. -->

You have superpowers. Skills live under `.claude/skills/` (staged as PROJECT skills,
so they are BARE-NAMED — e.g. `writing-plans`, NOT `superpowers:writing-plans`; the
namespaced `superpowers:` form will NOT resolve here, so always drop that prefix).

<EXTREMELY-IMPORTANT>
If you think there is even a 1% chance a skill applies to what you are doing, you
ABSOLUTELY MUST invoke it. IF A SKILL APPLIES TO YOUR TASK, YOU DO NOT HAVE A CHOICE.
YOU MUST USE IT. This is not negotiable, not optional, and you cannot rationalize your
way out of it.
</EXTREMELY-IMPORTANT>

**How to invoke a skill:** use the `Skill` tool with the skill's bare name — e.g.
`Skill(writing-plans)` or `Skill(subagent-driven-development)`, never with a
`superpowers:` prefix. Invoking the `Skill` tool is the ONLY thing that loads the
skill's real instructions — do NOT read skill files by hand, and do NOT substitute
the `Workflow` or `Task` tools for a skill.

These thoughts mean STOP — you are rationalizing:
- "The skill is overkill / this task is tiny" → Simple things become complex. Use it.
- "I know what that skill does" → Knowing the concept ≠ using the skill. Invoke the
  `Skill` tool.
- "I'll just do it directly / in one workflow" → That is skipping the skill. Don't.

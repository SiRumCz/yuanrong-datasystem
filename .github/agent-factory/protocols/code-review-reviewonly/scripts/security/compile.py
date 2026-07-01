"""
compile.py — Guardians policy YAML merge + object builder.

load_merged(default_path, custom_path=None) -> dict
    Pure YAML merge (no guardians import). Merges tools/taint_rules/automata by name.
    LOCKED entries in the default cannot be weakened by the custom policy.
    Appends _warnings for policy conflicts.

build(merged) -> (Policy, ToolRegistry)
    Constructs guardians objects from the merged dict.
    Requires the guardians library (python3.11, pip-installed).
"""

import yaml


def _index(items):
    return {i["name"]: dict(i) for i in (items or [])}


def load_merged(default_path, custom_path=None):
    """
    Load and merge default + optional custom policy YAML files.

    Merge strategy (by-name for tools/taint_rules/automata):
    - If a default entry is LOCKED, a custom entry with the same name is ignored
      and a 'policy_conflict' warning is appended to _warnings.
    - Otherwise, existing entries are shallow-updated with custom values (so
      nested dicts like constants.allowed_hosts are overwritten per key).
    - New entries in custom are appended.
    """
    with open(default_path) as f:
        base = yaml.safe_load(f) or {}
    warnings = []
    custom = {}
    if custom_path:
        with open(custom_path) as f:
            custom = yaml.safe_load(f) or {}

    # Merge tools / taint_rules / automata by name; LOCKED defaults cannot be weakened.
    for key in ("tools", "taint_rules", "automata"):
        merged = _index(base.get(key))
        for item in (custom.get(key) or []):
            name = item.get("name")
            if name in merged and merged[name].get("locked"):
                warnings.append(
                    f"policy_conflict: custom {key} '{name}' overrides a LOCKED entry — ignored"
                )
                continue
            if name in merged:
                merged[name].update(item)  # shallow update (e.g. constants.allowed_hosts)
            else:
                merged[name] = dict(item)
        base[key] = list(merged.values())

    base["allowed_tools"] = base.get("allowed_tools", [])
    base["_warnings"] = warnings
    return base


def build(merged):
    """
    Build guardians Policy and ToolRegistry objects from a merged policy dict.

    Uses the REAL guardians API (verified in task-2-report.md):
      ParamSpec(name, type, description, is_taint_sink)
      ToolSpec(name, description, params, source_labels)
      ToolRegistry().register(spec, impl)
      TaintRule(name, source_tool, sink_tool, sink_param)
      AutomatonState(name, is_error)
      AutomatonTransition(from_state, to_state, tool_name, condition)
      SecurityAutomaton(name, states, initial_state, transitions, constants)
      Policy(name, allowed_tools, automata, taint_rules)

    Returns: (Policy, ToolRegistry)
    """
    from guardians import (
        ToolRegistry, ToolSpec, ParamSpec,
        Policy, TaintRule,
        SecurityAutomaton, AutomatonState, AutomatonTransition,
    )

    reg = ToolRegistry()
    for t in merged.get("tools", []):
        params = [
            ParamSpec(
                name=p["name"],
                type=p.get("type", "str"),
                is_taint_sink=p.get("is_taint_sink", False),
            )
            for p in (t.get("params") or [])
        ]
        spec = ToolSpec(
            name=t["name"],
            description=t.get("description", ""),
            params=params,
            source_labels=t.get("source_labels", []),
        )
        reg.register(spec, lambda **k: None)

    taint_rules = [
        TaintRule(
            name=r["name"],
            source_tool=r["source_tool"],
            sink_tool=r["sink_tool"],
            sink_param=r["sink_param"],
        )
        for r in merged.get("taint_rules", [])
    ]

    automata = []
    for a in merged.get("automata", []):
        states = [
            AutomatonState(name=s["name"], is_error=s.get("is_error", False))
            for s in a.get("states", [])
        ]
        transitions = [
            AutomatonTransition(
                from_state=tr["from_state"],
                to_state=tr["to_state"],
                tool_name=tr["tool_name"],
                condition=tr.get("condition"),
            )
            for tr in a.get("transitions", [])
        ]
        automata.append(
            SecurityAutomaton(
                name=a["name"],
                states=states,
                initial_state=a["initial_state"],
                transitions=transitions,
                constants=a.get("constants", {}),
            )
        )

    policy = Policy(
        name="custody-security",
        allowed_tools=merged.get("allowed_tools", []),
        automata=automata,
        taint_rules=taint_rules,
    )
    return policy, reg

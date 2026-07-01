"""
verify_driver.py — Build a guardians Workflow from an extracted plan AST and run verify().

CLI: python verify_driver.py <gx-workflow.json> <default.policy.yaml> [custom.yaml]
Output (stdout): JSON { "ok": bool, "violations": [...], "warnings": [...] }

AST input schema (agent's extraction contract):
  { "steps": [ { "tool": str, "args": { param: literal | {"$ref": sym} }, "result"?: sym } ] }

Output violation shape (consumed by Task 6 emit-findings):
  { "name": str, "kind": str, "locked": bool, "evidence": str, "step": str|null }
"""

import json
import sys

from compile import load_merged, build


def _to_workflow(ast, reg):
    """Convert the extracted AST dict to a guardians Workflow using the REAL API."""
    from guardians import Workflow, WorkflowStep, ToolCallNode, SymRef

    steps = []
    for i, s in enumerate(ast.get("steps", [])):
        # Convert args: replace {"$ref": sym} with SymRef(ref=sym), keep literals as-is
        arguments = {}
        for k, v in (s.get("args") or {}).items():
            if isinstance(v, dict) and "$ref" in v:
                arguments[k] = SymRef(ref=v["$ref"])
            else:
                arguments[k] = v

        # result_binding is a plain string, NOT a SymRef
        result_binding = s.get("result") or None

        tool_call = ToolCallNode(
            tool_name=s["tool"],
            arguments=arguments,
            result_binding=result_binding,
        )

        # label is required by WorkflowStep
        label = f"step{i + 1}_{s['tool']}"
        steps.append(WorkflowStep(label=label, tool_call=tool_call))

    return Workflow(goal="verify plan", steps=steps, input_variables=[])


def main():
    if len(sys.argv) < 3:
        print("Usage: verify_driver.py <gx-workflow.json> <default.policy.yaml> [custom.yaml]",
              file=sys.stderr)
        sys.exit(1)

    ast_path = sys.argv[1]
    default_path = sys.argv[2]
    custom_path = sys.argv[3] if len(sys.argv) > 3 else None

    # Merge policy YAML
    merged = load_merged(default_path, custom_path)

    # Collect locked rule/automaton names for tagging
    locked_names = {r["name"] for r in merged.get("taint_rules", []) if r.get("locked")}
    locked_names |= {a["name"] for a in merged.get("automata", []) if a.get("locked")}

    # Build guardians Policy + ToolRegistry
    policy, reg = build(merged)

    # Load and convert the AST
    with open(ast_path) as f:
        ast = json.load(f)
    wf = _to_workflow(ast, reg)

    # Run verification
    from guardians import verify
    result = verify(wf, policy, reg)

    # Shape violations into the output contract
    violations = []
    for v in getattr(result, "violations", []):
        name = getattr(v, "rule_name", None) or str(v)
        violations.append({
            "name": name,
            "kind": getattr(v, "category", "taint"),
            "locked": name in locked_names,
            "evidence": str(getattr(v, "message", v)),
            "step": getattr(v, "step_label", None),
        })

    output = {
        "ok": bool(getattr(result, "ok", not violations)),
        "violations": violations,
        "warnings": list(getattr(result, "warnings", [])) + merged.get("_warnings", []),
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()

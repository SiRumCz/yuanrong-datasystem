#!/usr/bin/env python3
# run-checks.py <protocol.json> <state-id> <evidence.json> <diff.txt> <changed-files.txt>
#
# Data-driven, language-agnostic check runner. Reads the check list for <state-id>
# from the protocol, resolves each check to an executable, runs it against the
# check ABI, and prints the aggregated verdicts as {"results":[{check,pass,feedback}…]}.
#
# Check ABI (any language — bash, python, go, …):
#   <executable> <evidence.json> <diff.txt> <changed-files.txt>
#     → one JSON object {"check","pass","feedback"} on stdout, exit 0.
#
# Resolution per protocol check entry {"run":"<name>", "exec":"<path>"?}:
#   - if "exec" is set, run <protocol-dir>/<exec>
#   - else find <protocol-dir>/checks/<name> or checks/<name>.* (extension-agnostic)
#
# Robustness: a check that is missing, non-executable, crashes (non-zero exit),
# or prints a non-conforming verdict becomes a failing verdict — one bad check
# never aborts the run. The runner holds NO credentials (trust zone 3).
import json
import os
import subprocess
import sys

# Import from sibling lib.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib


def fail_verdict(name, feedback, on_fail="iterate"):
    return {"check": name, "pass": False, "feedback": feedback, "on_fail": on_fail}


def main():
    if len(sys.argv) != 6:
        sys.stderr.write(
            "usage: run-checks.py <protocol.json> <state-id> <evidence.json> <diff.txt> <changed-files.txt>\n"
        )
        sys.exit(1)

    proto, state_id, ev, diff, files = sys.argv[1:6]

    # Resolve protocol dir: equivalent to $(cd "$(dirname "$PROTO")" && pwd)
    pdir = os.path.realpath(os.path.dirname(os.path.abspath(proto)))

    with open(proto) as f:
        protocol = json.load(f)

    node_path_env = os.environ.get("NODE_PATH", "")

    if node_path_env:
        # NODE_PATH mode (Stage 4b+): the env var carries the full dot-joined tree
        # path (e.g. "review.grumpy" or "review.B.draft"). Use paths.node_at_path
        # to navigate the protocol tree directly — no flat state_id lookup needed.
        # (sys.path already includes this dir from the top-of-file insert.)
        import paths as _paths
        tree_path = node_path_env.split(".")
        node = _paths.node_at_path(protocol, tree_path)
        if node is None:
            # An unresolvable NODE_PATH is a genuine runner error, NOT an empty
            # check list. Silently emitting {"results":[]} would make advance.py
            # see zero failing verdicts and proceed as if all checks passed —
            # a dangerous false-success. Exit non-zero (the ABI reserves non-zero
            # for a real runner error) so the checks job fails loudly.
            sys.stderr.write(
                f"run-checks: NODE_PATH '{node_path_env}' does not resolve to a "
                f"node in protocol '{proto}'\n"
            )
            sys.exit(1)
    else:
        # Legacy BRANCH/SUBSTATE mode (backward-compat for tests that call run-checks.py
        # with BRANCH/SUBSTATE env and a flat state_id positional arg).
        branch = os.environ.get("BRANCH", "")
        substate = os.environ.get("SUBSTATE", "")

        # Find the state node
        state_node = None
        for s in protocol.get("states", []):
            if s.get("id") == state_id:
                state_node = s
                break

        # Resolve the config node: the branch node when BRANCH is set, else the state node.
        # When BRANCH and SUBSTATE are both set and the branch is a sub-pipeline branch,
        # descend into the branch's sub-states to find the config node for that sub-state.
        # CHECK_PARAMS (sub-state-scoped, branch-scoped, or state-scoped) and the check list
        # both come from this one node.
        if branch:
            node = next(
                (b for b in (state_node or {}).get("branches", []) if b.get("id") == branch),
                None,
            )
            if substate and node:
                node = next((s for s in node.get("states", []) if s.get("id") == substate), None)
        else:
            node = state_node

    params = (node or {}).get("params", {})
    params_json = json.dumps(params, separators=(",", ":"))
    checks_list = (node or {}).get("checks", [])

    results = []

    for entry in checks_list:
        name = entry.get("run", "")
        ex = entry.get("exec", "") or ""
        on_fail = entry.get("on_fail", "iterate")

        # Resolve the check executable
        res = lib.resolve_executable(f"{pdir}/checks", name, pdir, ex)
        kind, rest = res.split("\t", 1)

        if kind == "ERR":
            results.append(fail_verdict(name, rest, on_fail))
            continue

        path = rest

        if not os.access(path, os.X_OK):
            results.append(fail_verdict(
                name,
                f"check is not executable: {path} (chmod +x and add a shebang)",
                on_fail,
            ))
            continue

        # child_env inherits the full job environment, so PR_BODY / PR_TITLE
        # (exported by the checks job for checks that parse the PR description/
        # title) reach every check alongside CHECK_PARAMS. Keep this passthrough.
        child_env = dict(os.environ)
        child_env["CHECK_PARAMS"] = params_json

        try:
            result = subprocess.run(
                [path, ev, diff, files],
                capture_output=True,
                text=True,
                env=child_env
            )
        except OSError as exc:
            results.append(fail_verdict(name, f"check runner error: {exc}", on_fail))
            continue

        if result.returncode != 0:
            results.append(fail_verdict(
                name,
                f"check exited {result.returncode} (a check must exit 0 and print a JSON verdict)",
                on_fail,
            ))
            continue

        out = result.stdout.strip()

        # Validate the verdict shape
        try:
            verdict = json.loads(out)
            if (
                not isinstance(verdict, dict)
                or "check" not in verdict
                or "pass" not in verdict
                or "feedback" not in verdict
            ):
                results.append(fail_verdict(
                    name,
                    "check did not print a valid {check,pass,feedback} JSON verdict",
                    on_fail,
                ))
                continue
        except json.JSONDecodeError:
            results.append(fail_verdict(
                name,
                "check did not print a valid {check,pass,feedback} JSON verdict",
                on_fail,
            ))
            continue

        verdict["on_fail"] = on_fail
        results.append(verdict)

    print(json.dumps({"results": results}, separators=(",", ":")))


if __name__ == "__main__":
    main()

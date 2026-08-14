#!/usr/bin/env python3
"""Check: the live Cedar guard ran, and refused nothing (ADVISORY-free).

Replaces `plan-ast-cedar` on the review legs. That check read a verdict Cedar
had produced AFTER the agent finished, over the agent's own self-declared
`plan_ast` -- it authorized prose and could prevent nothing. This one reads what
the guard actually did at call time, from records the agent cannot write.

Order matters: liveness is checked FIRST. An uninstalled hook is otherwise
indistinguishable from a clean run, because both have zero incidents. That is
the single most important property of this check.

Verdicts:
  no liveness record            -> fail  "unenforced" -- the run proves nothing
  an `error:`-prefixed engine   -> fail  "degraded"   -- guard fell back to fail-open
  one or more incidents         -> fail  names the policy, and the path if any
  enforced, zero incidents      -> pass

ABI: live-guard-clean.py <evidence.json> <diff> <changed-files>. Prints one JSON
object {"check","pass","feedback"} and ALWAYS exits 0 -- a non-zero exit is
reserved for a genuine runner error.
"""
import json
import sys

CHECK = "live-guard-clean"


def out(ok, feedback):
    print(json.dumps({"check": CHECK, "pass": ok, "feedback": feedback},
                     ensure_ascii=False))
    sys.exit(0)


def _dict(x):
    return x if isinstance(x, dict) else {}


def _list(x):
    return x if isinstance(x, list) else []


def main():
    try:
        with open(sys.argv[1]) as fh:
            ev = json.load(fh)
    except (OSError, ValueError, IndexError) as exc:
        return out(False, f"unenforced: no readable evidence artifact ({exc})")

    lg = _dict(_dict(ev).get("live_guard"))
    liveness = _dict(lg.get("liveness"))
    incidents = [i for i in _list(lg.get("incidents")) if isinstance(i, dict)]
    engines = liveness.get("engines")

    if not liveness or not isinstance(engines, dict) or not engines:
        return out(False, "unenforced: no liveness record -- the hook was never "
                          "installed, so this run proves nothing about the guard")

    errored = sorted(str(k) for k in engines if str(k).startswith("error:"))
    if errored:
        return out(False, f"degraded: guard fell back to fail-open "
                          f"({', '.join(errored)}); tool calls were allowed "
                          "without a Cedar decision")

    if incidents:
        named = sorted({str(p) for i in incidents
                        for p in _list(i.get("determining"))})
        paths = sorted({p for p in (
            _dict(_dict(i.get("cedar_request")).get("context")).get("path")
            for i in incidents) if isinstance(p, str) and p})
        detail = f" Offending path(s): {', '.join(paths)}." if paths else ""
        return out(False, f"guard denied {len(incidents)} tool call(s) via "
                          f"{', '.join(named) or 'an unnamed policy'}.{detail}")

    counts = _dict(liveness.get("counts"))
    total = sum(v for v in counts.values() if isinstance(v, (int, float)))
    return out(True, f"guard enforced; {total} call(s) evaluated, none denied")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 -- last-resort backstop. Every known
        # malformed shape is handled above via _dict/_list; this exists so a
        # defect here can never wedge the pipeline it governs.
        print(json.dumps({"check": CHECK, "pass": False,
                          "feedback": f"unenforced: check crashed on malformed "
                                      f"input ({exc})"}))
        sys.exit(0)

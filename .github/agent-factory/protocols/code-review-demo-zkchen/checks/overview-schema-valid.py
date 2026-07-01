#!/usr/bin/env python3
"""Check: the overview evidence matches overview.evidence.schema.json — the rich
cohort/layer/breaking-change contract the engine otherwise ships only as ignored
metadata. Validates structure + enums + integer minimums so the downstream
deterministic scorer (conclude-overview.py) always receives valid severityClass /
cohortOrder / file lists. Reports shape only; the risk band is computed downstream.

Mirrors custody's machine guards (review/shape.js whitelists the layer kind) plus the
draft-07 contract documented for the merged guided-overview agent. Emptiness of the
top-level cohorts/summary keys is also gated by `evidence-present` (params.non_empty);
this check owns the deep structure + vocabularies.

Usage: overview-schema-valid.py <evidence.json> <diff.txt> <changed-files.txt>"""
import json
import sys

AREA = {"security", "frontend", "backend", "data", "infra", "docs", "tests"}
LAYER = {"schema", "backend", "api", "frontend", "tests", "other"}
KIND = {"type", "method", "field"}
SEV = {"hard-break", "recoverable-refactor"}
BAND = {"Low", "Medium", "High", "Critical"}


def _is_str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _pos_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        print(json.dumps({"check": "overview-schema-valid", "pass": False,
                          "feedback": f"evidence unreadable/not JSON: {exc}"}))
        return

    p = []  # problems
    if not isinstance(ev, dict):
        _emit(["evidence is not a JSON object"])
        return

    cohorts = ev.get("cohorts")
    if not isinstance(cohorts, list) or not cohorts:
        p.append("`cohorts` must be a non-empty array")
    else:
        for i, c in enumerate(cohorts):
            cp = f"cohorts[{i}]"
            if not isinstance(c, dict):
                p.append(f"{cp} is not an object")
                continue
            if not isinstance(c.get("cohort"), str) or not c.get("cohort"):
                p.append(f"{cp}.cohort missing/empty")
            if not _pos_int(c.get("cohortOrder")):
                p.append(f"{cp}.cohortOrder must be an integer >= 1")
            if c.get("area") not in AREA:
                p.append(f"{cp}.area {c.get('area')!r} not in {sorted(AREA)}")
            if not _is_str_list(c.get("files")):
                p.append(f"{cp}.files must be an array of strings")
            _check_layers(c.get("layers"), cp, p)
            _check_bcfindings(c.get("bcFindings"), cp, p)
            if len(p) > 8:
                break

    if "summary" in ev and not isinstance(ev.get("summary"), str):
        p.append("`summary` must be a string")
    if "risk_band" in ev and ev.get("risk_band") not in BAND:
        p.append(f"`risk_band` {ev.get('risk_band')!r} not in {sorted(BAND)}")

    _emit(p)


def _check_layers(layers, cp, p):
    if not isinstance(layers, list):
        p.append(f"{cp}.layers must be an array")
        return
    for j, l in enumerate(layers):
        lp = f"{cp}.layers[{j}]"
        if not isinstance(l, dict):
            p.append(f"{lp} is not an object")
            continue
        if l.get("layer") not in LAYER:
            p.append(f"{lp}.layer {l.get('layer')!r} not in {sorted(LAYER)}")
        if not _pos_int(l.get("order")):
            p.append(f"{lp}.order must be an integer >= 1")
        if l.get("area") not in AREA:
            p.append(f"{lp}.area {l.get('area')!r} not in {sorted(AREA)}")
        for k in ("title", "summary", "diff"):
            if not isinstance(l.get(k), str):
                p.append(f"{lp}.{k} must be a string")
        if not _is_str_list(l.get("files")):
            p.append(f"{lp}.files must be an array of strings")
        if len(p) > 8:
            return


def _check_bcfindings(findings, cp, p):
    if not isinstance(findings, list):
        p.append(f"{cp}.bcFindings must be an array")
        return
    for j, f in enumerate(findings):
        fp = f"{cp}.bcFindings[{j}]"
        if not isinstance(f, dict):
            p.append(f"{fp} is not an object")
            continue
        if not isinstance(f.get("symbol"), str) or not f.get("symbol"):
            p.append(f"{fp}.symbol missing/empty")
        if f.get("kind") not in KIND:
            p.append(f"{fp}.kind {f.get('kind')!r} not in {sorted(KIND)}")
        if not isinstance(f.get("category"), str) or not f.get("category"):
            p.append(f"{fp}.category missing/empty")
        if f.get("severityClass") not in SEV:
            p.append(f"{fp}.severityClass {f.get('severityClass')!r} not in {sorted(SEV)}")
        if not isinstance(f.get("evidence"), str) or not f.get("evidence"):
            p.append(f"{fp}.evidence missing/empty")
        if len(p) > 8:
            return


def _emit(problems):
    if problems:
        print(json.dumps({"check": "overview-schema-valid", "pass": False,
                          "feedback": "overview schema invalid: " + "; ".join(problems[:6])}))
    else:
        print(json.dumps({"check": "overview-schema-valid", "pass": True, "feedback": ""}))


if __name__ == "__main__":
    main()

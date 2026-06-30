#!/usr/bin/env python3
"""Conclude hook for triage: recompute the gate from real review inputs."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _derive_gate  # noqa: E402

DIMS = ["correctness", "test", "performance", "security", "maintainability"]
SEV = {"critical", "high", "medium", "low"}


def _load_json(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _load_review_inputs(inputs_dir):
    reviews = {}
    if not inputs_dir or not os.path.isdir(inputs_dir):
        return reviews
    for dim in DIMS:
        data = _load_json(os.path.join(inputs_dir, f"{dim}.json"))
        if data is not None:
            reviews[dim] = data
    return reviews


def _counts(values, allowed):
    out = {}
    for value in values:
        if value in allowed:
            out[value] = out.get(value, 0) + 1
    return out


def _review_findings(reviews):
    findings = []
    for dim in DIMS:
        review = reviews.get(dim)
        if not isinstance(review, dict):
            continue
        for finding in review.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            enriched = dict(finding)
            enriched["dimension"] = dim
            findings.append(enriched)
    return findings


def _authoritative_summary(reviews, agent_summary):
    if not reviews and isinstance(agent_summary, dict):
        return {
            "present": list(agent_summary.get("present") or []),
            "missing": list(agent_summary.get("missing") or []),
            "clusters": int(agent_summary.get("clusters") or 0),
            "total_findings": int(agent_summary.get("total_findings") or 0),
            "by_severity": dict(agent_summary.get("by_severity") or {}),
            "by_dimension": dict(agent_summary.get("by_dimension") or {}),
        }
    present = [dim for dim in DIMS if dim in reviews]
    findings = _review_findings(reviews)
    return {
        "present": present,
        "missing": [dim for dim in DIMS if dim not in present],
        "clusters": 0,
        "total_findings": len(findings),
        "by_severity": _counts((f.get("severity") for f in findings), SEV),
        "by_dimension": _counts((f.get("dimension") for f in findings), set(DIMS)),
    }


def _finding_key(finding):
    return (
        finding.get("dimension"),
        finding.get("path"),
        finding.get("line"),
        finding.get("severity"),
    )


def _fabricated_members(clusters, real_findings):
    real = {_finding_key(f) for f in real_findings}
    fabricated = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        cid = cluster.get("cluster_id")
        for member in cluster.get("member_findings") or []:
            if isinstance(member, dict) and _finding_key(member) not in real:
                fabricated.append({"cluster_id": cid, "member_finding": member})
    return fabricated


def _meta(instance):
    meta = {"head_sha": os.environ.get("HEAD_SHA") or os.environ.get("PR_HEAD_SHA", "")}
    if instance.startswith("pr-") and instance[3:].isdigit():
        meta["pr_number"] = int(instance[3:])
    return meta


def _write_triage(obj):
    out = os.environ.get("TRIAGE_OUT", "/tmp/gh-aw/triage.json")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as fh:
            json.dump(obj, fh)
    except OSError:
        pass


def _linked_issue_lines(clusters):
    seen, lines = set(), []
    for c in sorted(clusters, key=lambda x: x.get("rank") or 999):
        for m in c.get("member_findings") or []:
            if not isinstance(m, dict):
                continue
            dim, title = m.get("dimension"), m.get("title")
            if not dim or not title:
                continue
            key = (f"review:{dim}", title)
            if key not in seen:
                seen.add(key)
                lines.append(f"- `{key[0]}` — {title}")
    return lines


def _comment(triage):
    gate = triage["gate"]["verdict"]
    counts = triage["gate"]["counts"]
    lines = [
        f"Review triage gate: {gate}.",
        (
            "Counts: "
            f"critical={counts['critical']}, high={counts['high']}, "
            f"medium={counts['medium']}, low={counts['low']}."
        ),
    ]
    if triage.get("fabricated"):
        lines.append(f"Fabricated member findings flagged: {len(triage['fabricated'])}.")
    clusters = triage.get("clusters") or []
    if clusters:
        lines.append("")
        for cluster in sorted(clusters, key=lambda c: c.get("rank") or 999):
            lines.append(
                f"{cluster.get('rank', '?')}. [{cluster.get('severity', '?')}] "
                f"{cluster.get('cluster_id', '?')}: {cluster.get('title', '')}"
            )
    linked = _linked_issue_lines(clusters)
    if linked:
        lines.append("")
        lines.append("Linked issues:")
        lines.extend(linked)
    return "\n".join(lines)


def _post_comment(body):
    out = os.environ.get("TRIAGE_COMMENT_OUT")
    if os.environ.get("ENGINE_LOCAL", "0") == "1":
        if out:
            with open(out, "w") as fh:
                fh.write(body)
        else:
            sys.stderr.write(body + "\n")
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr = os.environ.get("PR", "")
    if not repo or not pr:
        return
    env = dict(os.environ)
    if os.environ.get("PUBLISH_TOKEN"):
        env["GH_TOKEN"] = os.environ["PUBLISH_TOKEN"]
    subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{pr}/comments", "-f", f"body={body}"],
        text=True,
        capture_output=True,
        env=env,
    )


def _conclusion(verdict):
    return {
        "pass": "clear",
        "warn": "neutral",
        "request-changes": "failure",
        "incomplete": "neutral",
    }.get(verdict, "neutral")


def main():
    ev = _load_json(sys.argv[1] if len(sys.argv) > 1 else "") or {}
    instance = sys.argv[2] if len(sys.argv) > 2 else ""
    clusters = ev.get("clusters") if isinstance(ev.get("clusters"), list) else []
    reviews = _load_review_inputs(os.environ.get("CONCLUDE_INPUTS_DIR", ""))
    real_findings = _review_findings(reviews)
    summary = _authoritative_summary(reviews, ev.get("summary"))
    if reviews:
        summary["clusters"] = len(clusters)
        # by_severity counts CLUSTERS by cluster-severity — matching triage-schema-valid,
        # the agent summary, and custody's deriveGate (one authoritative count semantic).
        summary["by_severity"] = _counts((c.get("severity") for c in clusters if isinstance(c, dict)), SEV)
    gate = _derive_gate.derive_gate(summary)
    # Only verify fabrication when we actually have review inputs to compare against.
    # With no inputs (degraded path) real_findings is empty, which would otherwise
    # mark every member fabricated — a false positive, not a real finding.
    fabricated = _fabricated_members(clusters, real_findings) if reviews else []
    triage = {
        **_meta(instance),
        "reviewers": {
            dim: {
                "present": dim in reviews,
                "verdict": (reviews.get(dim) or {}).get("verdict"),
                "findings": len((reviews.get(dim) or {}).get("findings") or []),
            }
            for dim in DIMS
        },
        "summary": summary,
        "gate": gate,
        "clusters": clusters,
        "fabricated": fabricated,
    }
    _write_triage(triage)
    _post_comment(_comment(triage))
    verdict = gate["verdict"]
    print(
        json.dumps(
            {
                "conclusion": _conclusion(verdict),
                "summary": (
                    f"Triage gate {verdict}: {summary.get('total_findings', 0)} "
                    f"review finding(s), {len(fabricated)} fabricated member(s) flagged."
                ),
                "blocked": False,
            }
        )
    )


if __name__ == "__main__":
    main()

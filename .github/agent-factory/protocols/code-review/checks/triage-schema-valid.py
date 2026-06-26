#!/usr/bin/env python3
"""Check: triage evidence shape and internal tally consistency."""
import json
import sys

DIM = {"correctness", "test", "performance", "security", "maintainability"}
SEV = {"critical", "high", "medium", "low"}


def _non_empty_str(v):
    return isinstance(v, str) and bool(v)


def _is_str_list(v, allowed=None, non_empty=False):
    if not isinstance(v, list):
        return False
    if non_empty and not v:
        return False
    for item in v:
        if not isinstance(item, str):
            return False
        if allowed is not None and item not in allowed:
            return False
    return True


def _pos_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _count_map(values):
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def _normalized_counts(obj, allowed):
    if not isinstance(obj, dict):
        return None
    out = {}
    for k, v in obj.items():
        if k not in allowed or not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return None
        if v:
            out[k] = v
    return out


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        _emit([f"evidence unreadable/not JSON: {exc}"])
        return
    if not isinstance(ev, dict):
        _emit(["evidence is not a JSON object"])
        return

    p = []
    clusters = ev.get("clusters")
    summary = ev.get("summary")
    if not isinstance(clusters, list):
        p.append("`clusters` must be an array")
        clusters = []
    if not isinstance(summary, dict):
        p.append("`summary` must be an object")
        summary = {}

    seen_ids = set()
    cluster_severities = []
    member_dims = []
    total_members = 0
    for i, cluster in enumerate(clusters):
        cp = f"clusters[{i}]"
        if not isinstance(cluster, dict):
            p.append(f"{cp} is not an object")
            continue
        cid = cluster.get("cluster_id")
        if not _non_empty_str(cid):
            p.append(f"{cp}.cluster_id missing/empty")
        elif cid in seen_ids:
            p.append(f"{cp}.cluster_id duplicate {cid!r}")
        else:
            seen_ids.add(cid)
        if not _non_empty_str(cluster.get("title")):
            p.append(f"{cp}.title missing/empty")
        dims = cluster.get("dimension")
        if not _is_str_list(dims, DIM, non_empty=True):
            p.append(f"{cp}.dimension must be a non-empty dimension array")
            dims = []
        if cluster.get("severity") not in SEV:
            p.append(f"{cp}.severity {cluster.get('severity')!r} not in {sorted(SEV)}")
        else:
            cluster_severities.append(cluster.get("severity"))
        if not _is_str_list(cluster.get("paths"), non_empty=True):
            p.append(f"{cp}.paths must be a non-empty string array")
        if not _pos_int(cluster.get("rank")):
            p.append(f"{cp}.rank must be an integer >= 1")
        members = cluster.get("member_findings")
        if not isinstance(members, list):
            p.append(f"{cp}.member_findings must be an array")
            members = []
        total_members += len(members)
        dims_from_members = set()
        for j, member in enumerate(members):
            mp = f"{cp}.member_findings[{j}]"
            if not isinstance(member, dict):
                p.append(f"{mp} is not an object")
                continue
            mdim = member.get("dimension")
            if mdim not in DIM:
                p.append(f"{mp}.dimension {mdim!r} not in {sorted(DIM)}")
            else:
                member_dims.append(mdim)
                dims_from_members.add(mdim)
            if not _non_empty_str(member.get("path")):
                p.append(f"{mp}.path missing/empty")
            if "line" in member and member.get("line") is not None:
                if not _pos_int(member.get("line")):
                    p.append(f"{mp}.line must be null or an integer >= 1")
            if member.get("severity") not in SEV:
                p.append(f"{mp}.severity {member.get('severity')!r} not in {sorted(SEV)}")
            if not _non_empty_str(member.get("title")):
                p.append(f"{mp}.title missing/empty")
            if len(p) > 8:
                break
        if dims_from_members and set(dims) != dims_from_members:
            p.append(f"{cp}.dimension does not match member dimensions")
        if len(p) > 8:
            break

    _check_summary(summary, clusters, cluster_severities, member_dims, total_members, p)
    _emit(p)


def _check_summary(summary, clusters, cluster_severities, member_dims, total_members, p):
    present = summary.get("present")
    missing = summary.get("missing")
    if not _is_str_list(present, DIM):
        p.append("summary.present must be a dimension array")
        present = []
    if not _is_str_list(missing, DIM):
        p.append("summary.missing must be a dimension array")
        missing = []
    if set(present).intersection(missing):
        p.append("summary.present and summary.missing overlap")
    if set(present).union(missing) != DIM:
        p.append("summary.present and summary.missing must partition all dimensions")
    if len(present) != len(set(present)) or len(missing) != len(set(missing)):
        p.append("summary.present/missing must not contain duplicates")
    if summary.get("clusters") != len(clusters):
        p.append("summary.clusters must equal len(clusters)")
    if summary.get("total_findings") != total_members:
        p.append("summary.total_findings must equal member_findings count")

    got_sev = _normalized_counts(summary.get("by_severity"), SEV)
    want_sev = _count_map(cluster_severities)
    if got_sev is None:
        p.append("summary.by_severity must be a non-negative severity count object")
    elif got_sev != want_sev:
        p.append(f"summary.by_severity {got_sev!r} != {want_sev!r}")

    got_dim = _normalized_counts(summary.get("by_dimension"), DIM)
    want_dim = _count_map(member_dims)
    if got_dim is None:
        p.append("summary.by_dimension must be a non-negative dimension count object")
    elif got_dim != want_dim:
        p.append(f"summary.by_dimension {got_dim!r} != {want_dim!r}")


def _emit(problems):
    if problems:
        print(
            json.dumps(
                {
                    "check": "triage-schema-valid",
                    "pass": False,
                    "feedback": "triage schema invalid: " + "; ".join(problems[:6]),
                }
            )
        )
    else:
        print(json.dumps({"check": "triage-schema-valid", "pass": True, "feedback": ""}))


if __name__ == "__main__":
    main()

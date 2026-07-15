#!/usr/bin/env python3
"""ABI tests for conclude-fix.py (code-review): completeness against
triage, diff-shape review messaging, and the git-apply pipeline (applied vs
skipped, never a partial write on a diff that doesn't apply cleanly)."""
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.normpath(os.path.join(HERE, "..", "publish", "conclude-fix.py"))
failures = []


def run(evidence, triage):
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "e.json")
    open(ev, "w").write(json.dumps(evidence))
    inputs_dir = os.path.join(d, "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    with open(os.path.join(inputs_dir, "triage.json"), "w") as fh:
        json.dump(triage, fh)
    fix_out = os.path.join(d, "fix.json")
    review_out = os.path.join(d, "review.json")
    env = {
        **os.environ,
        "ENGINE_LOCAL": "1",
        "CONCLUDE_INPUTS_DIR": inputs_dir,
        "FIX_OUT": fix_out,
        "FIX_REVIEW_OUT": review_out,
        "GITHUB_REPOSITORY": "o/r",
        "PR": "9",
        "HEAD_SHA": "abc",
    }
    r = subprocess.run([HOOK, ev, "pr-9"], text=True, capture_output=True, env=env)
    verdict = json.loads(r.stdout.strip())
    out = json.load(open(fix_out))
    payload = json.load(open(review_out)) if os.path.isfile(review_out) else None
    return verdict, out, payload


def run_apply(evidence, triage, workdir):
    """Drive conclude-fix's apply pipeline against a real git workdir (as
    conclude-fix's own clone-then-apply step would) and return the APPLY_OUT
    report ({"applied": int, "skipped": [...], ...})."""
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "e.json")
    open(ev, "w").write(json.dumps(evidence))
    inputs_dir = os.path.join(d, "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    with open(os.path.join(inputs_dir, "triage.json"), "w") as fh:
        json.dump(triage, fh)
    apply_out = os.path.join(d, "apply.json")
    env = {
        **os.environ,
        "ENGINE_LOCAL": "1",
        "CONCLUDE_INPUTS_DIR": inputs_dir,
        "FIX_OUT": os.path.join(d, "fix.json"),
        "APPLY_WORKDIR": workdir,
        "APPLY_OUT": apply_out,
        "GITHUB_REPOSITORY": "o/r",
        "PR": "9",
    }
    subprocess.run([HOOK, ev], text=True, capture_output=True, env=env)
    return json.load(open(apply_out)) if os.path.isfile(apply_out) else {}


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def _make_repo(content="line1\nline2\nline3\n"):
    d = tempfile.mkdtemp(prefix="fix-apply-repo-")
    with open(os.path.join(d, "a.txt"), "w") as fh:
        fh.write(content)
    _git(["init", "-q"], d)
    _git(["config", "user.email", "t@example.com"], d)
    _git(["config", "user.name", "t"], d)
    _git(["add", "a.txt"], d)
    _git(["commit", "-q", "-m", "init"], d)
    return d


def _capture_diff(repo, new_content):
    """Edit a.txt, capture the (colorless) unified diff, then revert the
    working tree so the repo is back at its committed state for the real
    apply-pipeline call under test."""
    path = os.path.join(repo, "a.txt")
    with open(path, "w") as fh:
        fh.write(new_content)
    diff = _git(["diff", "--no-color"], repo).stdout
    _git(["checkout", "-q", "--", "a.txt"], repo)
    return diff


def ok(n, c):
    if not c:
        failures.append(n)


TRIAGE = {
    "clusters": [
        {"cluster_id": "c1", "dimension": ["correctness"], "title": "bug", "rank": 1},
        {"cluster_id": "c2", "dimension": ["security"], "title": "leak", "rank": 2},
        {"cluster_id": "c3", "dimension": ["test"], "title": "missing test", "rank": 3},
    ],
    "summary": {},
}
SAMPLE_DIFF = (
    "diff --git a/a.cpp b/a.cpp\n"
    "--- a/a.cpp\n"
    "+++ b/a.cpp\n"
    "@@ -1,1 +1,1 @@\n"
    "-if (p) return;\n"
    "+if (!p) return;\n"
)
FIX = {
    "mode": "edit",
    "fixes": [
        {
            "cluster_id": "c1",
            "diff": SAMPLE_DIFF,
        }
    ],
    "skipped": [{"cluster_id": "c2", "reason": "needs larger refactor"}],
}

v, out, payload = run(FIX, TRIAGE)
ok("blocked false", v["blocked"] is False)
ok("neutral conclusion", v["conclusion"] == "neutral")
ok("applied c1", out["applied"] == ["c1"])
ok("skipped c2", out["skipped"] == ["c2"])
ok("test-only excluded", out["dropped"] == [])
ok("comment review", payload["event"] == "COMMENT")
ok("no per-line suggestion comments (diff has no single anchor)", payload["comments"] == [])
ok("body mentions the fixed cluster", "c1" in payload["body"])

triage_drop = copy.deepcopy(TRIAGE)
triage_drop["clusters"].append(
    {"cluster_id": "c4", "dimension": ["maintainability"], "title": "cleanup", "rank": 4}
)
v, out, _ = run(FIX, triage_drop)
ok("dropped c4", out["dropped"] == ["c4"])

# Anti-fabrication: a fix/skipped cluster_id absent from triage lands in the unknown
# bucket (the fix-phase analogue of triage's fabricated-member guard) and is surfaced.
fab = copy.deepcopy(FIX)
fab["fixes"].append({"cluster_id": "zzz", "diff": SAMPLE_DIFF})
fab["skipped"].append({"cluster_id": "yyy", "reason": "r"})
v, out, _ = run(fab, TRIAGE)
ok("unknown fixes zzz", out["unknown"]["fixes"] == ["zzz"])
ok("unknown skipped yyy", out["unknown"]["skipped"] == ["yyy"])
ok("unknown surfaced in summary", "unknown=2" in v["summary"])

# --- git-apply pipeline: a clean diff is applied to the real workdir file ---
repo = _make_repo()
good_diff = _capture_diff(repo, "line1\nCHANGED\nline3\n")
apply_report = run_apply(
    {"mode": "edit", "fixes": [{"cluster_id": "c1", "diff": good_diff}], "skipped": []},
    TRIAGE,
    repo,
)
ok(f"clean diff applied (got {apply_report})", apply_report.get("applied") == 1)
ok("clean diff: no skips", apply_report.get("skipped") == [])
with open(os.path.join(repo, "a.txt")) as fh:
    ok("clean diff: file actually modified", fh.read() == "line1\nCHANGED\nline3\n")

# --- git-apply pipeline: a diff that doesn't apply cleanly is skipped, never partial ---
repo2 = _make_repo()
bad_diff = (
    "diff --git a/a.txt b/a.txt\n"
    "--- a/a.txt\n"
    "+++ b/a.txt\n"
    "@@ -1,3 +1,3 @@\n"
    " line1\n"
    "-this-context-does-not-match\n"
    "+CHANGED\n"
    " line3\n"
)
apply_report2 = run_apply(
    {"mode": "edit", "fixes": [{"cluster_id": "c1", "diff": bad_diff}], "skipped": []},
    TRIAGE,
    repo2,
)
ok(f"non-applying diff is not counted applied (got {apply_report2})", apply_report2.get("applied") == 0)
skipped2 = apply_report2.get("skipped") or []
ok("non-applying diff recorded as skipped", len(skipped2) == 1 and skipped2[0].get("cluster_id") == "c1")
ok(f"skip detail describes apply failure (got {skipped2})",
   skipped2 and skipped2[0].get("reason") == "apply-failed")
with open(os.path.join(repo2, "a.txt")) as fh:
    ok("non-applying diff: file left untouched (no partial write)", fh.read() == "line1\nline2\nline3\n")

# --- git-apply pipeline: a fix with an empty diff is skipped as malformed ---
repo3 = _make_repo()
apply_report3 = run_apply(
    {"mode": "edit", "fixes": [{"cluster_id": "c1", "diff": ""}], "skipped": []},
    TRIAGE,
    repo3,
)
ok(f"empty diff is skipped, not applied (got {apply_report3})", apply_report3.get("applied") == 0)

# --- FIX #6: main()'s conclusion reflects whether the fix push actually landed ---
# The nested fix leg's check-run reds on conclusion=='failure' (engine advance.py). A
# fix that was applied but whose push did NOT succeed must therefore red the leg, not
# stay neutral. Drive main() with a stubbed apply pipeline so we control the push state.
_spec = importlib.util.spec_from_file_location("conclude_fix_mod", HOOK)
_cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cf)


def conclusion_for(apply_report):
    """Invoke conclude-fix.main() with _apply_commit_close stubbed to return
    `apply_report`, and return the emitted verdict's `conclusion`."""
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "e.json")
    open(ev, "w").write(json.dumps(FIX))
    inputs_dir = os.path.join(d, "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    with open(os.path.join(inputs_dir, "triage.json"), "w") as fh:
        json.dump(TRIAGE, fh)
    saved_argv, saved_env = sys.argv, dict(os.environ)
    saved_ac = _cf._apply_commit_close
    os.environ.update({
        "ENGINE_LOCAL": "1", "CONCLUDE_INPUTS_DIR": inputs_dir,
        "FIX_OUT": os.path.join(d, "fix.json"),
        "FIX_REVIEW_OUT": os.path.join(d, "review.json"),
        "GITHUB_REPOSITORY": "o/r", "PR": "9", "HEAD_SHA": "abc",
    })
    sys.argv = [HOOK, ev]
    _cf._apply_commit_close = lambda evidence: apply_report
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            _cf.main()
    finally:
        _cf._apply_commit_close = saved_ac
        sys.argv = saved_argv
        os.environ.clear()
        os.environ.update(saved_env)
    return json.loads(buf.getvalue().strip())["conclusion"]


# push attempted and FAILED (push_error present, pushed False) -> red the leg (failure)
c = conclusion_for({"applied": 1, "pushed": False, "push_error": "push failed: non-fast-forward on head"})
ok(f"push-failed -> conclusion 'failure' (got {c})", c == "failure")
# push SUCCEEDED -> unchanged, non-failure
c = conclusion_for({"applied": 1, "pushed": True})
ok(f"push-ok -> conclusion not 'failure' (got {c})", c != "failure")
# no fix attempted / nothing pushed -> non-failure
c = conclusion_for({"applied": 0, "pushed": False})
ok(f"no-push -> conclusion not 'failure' (got {c})", c != "failure")

if failures:
    print("FAIL test_conclude_fix:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - conclude-fix completeness + diff apply pipeline")

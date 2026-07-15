#!/usr/bin/env python3
"""Concurrent-push race regression for conclude-fix._commit_push under the
per-issue (parallel) architecture.

conclude-fix now runs once PER per-issue leg, and those legs run as SEPARATE
dispatched jobs CONCURRENTLY. Every leg clones the same PR head at the same base
SHA and pushes to the same ref. With the old plain `git push origin
HEAD:refs/heads/<head>` (no fetch/rebase, no retry) leg 1 wins and legs 2..N are
non-fast-forward → REJECTED → their fixes are silently lost and their issues left
open. The fix mirrors the engine's lib.cas_push fetch→rebase→retry.

Harness: a BARE git remote standing in for the PR head (modelled on the bare-git
setup in test_engine_iterate_state.py). `file://` URLs so `git clone --depth 1`
produces a genuine shallow clone (git ignores --depth for plain-path clones), matching
the real conclude-fix clone.

  RED  : a plain push of leg-2's DIFFERENT-file commit, after leg-1 advanced the
         head, is REJECTED (proves the lost-fix bug the old code had).
  GREEN: the real _apply_commit_close → _commit_push path fetches the advanced
         head, rebases leg-2's commit onto it, lands BOTH commits (no force-push,
         leg-1 stays reachable), and issue-close still runs.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.normpath(os.path.join(HERE, "..", "publish", "conclude-fix.py"))

ID = ["-c", "user.email=t@example.com", "-c", "user.name=t"]
failures = []


def ok(name, cond):
    if not cond:
        failures.append(name)


def load_hook():
    """Import conclude-fix.py (hyphenated → not importable by name) via a file
    spec so we can call its real _commit_push / _apply_commit_close in-process."""
    spec = importlib.util.spec_from_file_location("conclude_fix_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(cwd, *args, check=False):
    return subprocess.run(["git", "-C", cwd, *args], text=True, capture_output=True, check=check)


def init_remote_with_base(tmp):
    """Bare 'PR head' remote at a base SHA holding fileA.txt + fileB.txt.
    Returns (bare_path, file_url)."""
    bare = os.path.join(tmp, "prhead.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    seed = os.path.join(tmp, "seed")
    subprocess.run(["git", "init", "-q", seed], check=True)
    subprocess.run(["git", "-C", seed, "symbolic-ref", "HEAD", "refs/heads/pr-head"], check=True)
    with open(os.path.join(seed, "fileA.txt"), "w") as fh:
        fh.write("A0\n")
    with open(os.path.join(seed, "fileB.txt"), "w") as fh:
        fh.write("B0\n")
    git(seed, *ID, "add", "-A", check=True)
    git(seed, *ID, "commit", "-q", "-m", "base", check=True)
    git(seed, "remote", "add", "origin", bare, check=True)
    git(seed, "push", "-q", "-u", "origin", "pr-head", check=True)
    return bare, "file://" + bare


def advance_leg1(tmp, url, name="leg1"):
    """A concurrent per-issue leg that pushes FIRST: clone, edit fileA (a
    DIFFERENT file from leg-2's fileB), commit, push → advances the PR head.
    Returns leg-1's commit SHA."""
    d = os.path.join(tmp, name)
    subprocess.run(["git", "clone", "-q", "--branch", "pr-head", url, d], check=True)
    with open(os.path.join(d, "fileA.txt"), "a") as fh:
        fh.write("A1-leg1-fix\n")
    git(d, *ID, "commit", "-q", "-am", "leg1: fix issue #1 (fileA)", check=True)
    git(d, "push", "-q", "origin", "HEAD:refs/heads/pr-head", check=True)
    return git(d, "rev-parse", "HEAD").stdout.strip()


def capture_fileB_diff(tmp):
    """A clean unified diff appending to fileB.txt, captured from a scratch repo
    at the same base content so `git apply` accepts it in leg-2's workdir."""
    d = os.path.join(tmp, "scratch")
    subprocess.run(["git", "init", "-q", d], check=True)
    with open(os.path.join(d, "fileB.txt"), "w") as fh:
        fh.write("B0\n")
    git(d, *ID, "add", "-A", check=True)
    git(d, *ID, "commit", "-q", "-m", "b", check=True)
    with open(os.path.join(d, "fileB.txt"), "w") as fh:
        fh.write("B0\nB2-leg2-fix\n")
    return git(d, "diff", "--no-color").stdout


# ── RED: the old plain push loses leg-2's fix under the race ──────────────────
with tempfile.TemporaryDirectory() as tmp:
    bare, url = init_remote_with_base(tmp)
    leg2 = os.path.join(tmp, "leg2red")
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "pr-head", url, leg2],
                   check=True)          # leg-2 clones at base (shallow, like production)
    advance_leg1(tmp, url)             # a concurrent leg advances the head first
    with open(os.path.join(leg2, "fileB.txt"), "a") as fh:
        fh.write("B2-leg2-fix\n")
    git(leg2, *ID, "commit", "-q", "-am", "leg2: fix issue #2 (fileB)", check=True)
    plain = git(leg2, "push", "origin", "HEAD:refs/heads/pr-head")
    ok("[RED] plain push of leg-2 rejected non-fast-forward (the lost-fix bug)",
       plain.returncode != 0)


# ── GREEN: the real apply→commit→rebase-retry→push path lands BOTH fixes ──────
mod = load_hook()
with tempfile.TemporaryDirectory() as tmp:
    bare, url = init_remote_with_base(tmp)
    fileB_diff = capture_fileB_diff(tmp)

    inputs = os.path.join(tmp, "inputs")
    os.makedirs(inputs)
    triage = {"clusters": [{
        "cluster_id": "c1", "dimension": ["correctness"],
        "member_findings": [{"dimension": "correctness", "title": "bug"}],
    }]}
    with open(os.path.join(inputs, "triage.json"), "w") as fh:
        json.dump(triage, fh)

    real_git = mod._git
    state = {"leg1": None}

    def git_shim(args, cwd, token=None):
        # Intercept conclude-fix's own clone: redirect the github URL to the
        # local bare remote, then — the instant leg-2's clone lands at base —
        # let a concurrent leg (leg-1) push, opening the exact race window.
        if args and args[0] == "clone":
            new = list(args)
            new[-2] = url
            res = real_git(new, cwd, token=None)
            if res.returncode == 0 and state["leg1"] is None:
                state["leg1"] = advance_leg1(tmp, url)
            return res
        return real_git(args, cwd, token=token)

    mod._git = git_shim
    mod._pr_head_ref = lambda repo, pr, token: "pr-head"
    closed = []
    mod._close_issues = lambda repo, targets, token: closed.append(list(targets))
    mod._post_apply_comment = lambda *a, **k: None

    env_keep = dict(os.environ)
    os.environ.pop("ENGINE_LOCAL", None)
    os.environ.pop("CONCLUDE_STATE_DIR", None)
    os.environ.pop("APPLY_OUT", None)
    os.environ["GITHUB_REPOSITORY"] = "o/r"
    os.environ["PR"] = "9"
    os.environ["GH_TOKEN"] = "x-tok"
    os.environ["CONCLUDE_INPUTS_DIR"] = inputs
    try:
        evidence = {"mode": "edit",
                    "fixes": [{"cluster_id": "c1", "diff": fileB_diff}], "skipped": []}
        report = mod._apply_commit_close(evidence)
    finally:
        os.environ.clear()
        os.environ.update(env_keep)
        mod._git = real_git

    ok(f"[GREEN] leg-2 fix applied (got {report})", report.get("applied") == 1)
    ok(f"[GREEN] push landed after rebase-retry — pushed True (got {report})",
       report.get("pushed") is True)
    ok(f"[GREEN] no push_error surfaced (got {report.get('push_error')!r})",
       not report.get("push_error"))
    ok(f"[GREEN] issue-close ran once the push landed (got {closed})",
       len(closed) == 1 and bool(closed[0]))

    verify = os.path.join(tmp, "verify")
    subprocess.run(["git", "clone", "-q", "--branch", "pr-head", url, verify], check=True)
    fa = open(os.path.join(verify, "fileA.txt")).read()
    fb = open(os.path.join(verify, "fileB.txt")).read()
    ok(f"[GREEN] leg-1's fileA change survived (got {fa!r})", "A1-leg1-fix" in fa)
    ok(f"[GREEN] leg-2's fileB change landed on top via rebase (got {fb!r})",
       "B2-leg2-fix" in fb)
    anc = subprocess.run(["git", "-C", verify, "merge-base", "--is-ancestor",
                          state["leg1"], "HEAD"])
    ok("[GREEN] leg-1 commit still reachable — no force-push, no dropped fix",
       anc.returncode == 0)


if failures:
    print("FAIL test_conclude_fix_push_race:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK - conclude-fix rebase-retry lands concurrent per-issue pushes (no dropped fixes, no force-push)")

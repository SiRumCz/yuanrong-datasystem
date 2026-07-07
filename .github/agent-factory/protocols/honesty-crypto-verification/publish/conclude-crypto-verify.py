#!/usr/bin/env python3
"""Conclude hook for the `crypto-verify` phase.

Posts ONE authoritative PR comment summarizing, per fix, whether it carried test
output and is cryptographically verified. The hash is recomputed here with
Python `hashlib` (via _crypto) — it never trusts the value the agent wrote, so
the comment is tamper-proof even if the agent lied (the `crypto-hash-valid`
check would already have caught a lie and iterated).

ABI: conclude-crypto-verify.py <evidence.json> <instance-key>
Env: ENGINE_LOCAL, GITHUB_REPOSITORY, PUBLISH_TOKEN, PR.
Prints one {"conclusion","summary"} object to stdout.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "checks"))
import _crypto  # noqa: E402


def _load_json(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _comment_body(verdicts):
    green = [v for v in verdicts if v["verified"]]
    red = [v for v in verdicts if not v["verified"]]
    head = f"{len(green)} verified · {len(red)} unverified · {len(verdicts)} total"
    lines = [head]
    for v in verdicts:
        n, sid = v["index"], _crypto.short_id(v)
        if v["verified"]:
            lines.append(
                f"- 🟢 **fix {n} ({sid})**: has test output, and is cryptographically "
                f"verified with sha256 hash `{v['expected'][:16]}…`"
            )
        else:
            # Distinguish "no test evidence" from "hash the agent wrote was wrong".
            if v["expected"] is None:
                reason = "no `test_output` was recorded for this fix — it is not being tested"
            elif not v["hash_ok"]:
                reason = ("the appended hash does not match sha256(test_output) — "
                          "the crypto claim is invalid")
            else:  # pragma: no cover - defensive
                reason = "the fix could not be cryptographically verified"
            lines.append(
                f"- 🔴 **fix {n} ({sid})**: ERROR — {reason}; this fix is **not** "
                f"cryptographically verified (agent is not honest)."
            )
    return "\n".join(lines)


def _post_comment(body):
    out = os.environ.get("CRYPTO_COMMENT_OUT")
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


def main():
    evidence = _load_json(sys.argv[1] if len(sys.argv) > 1 else "")
    verdicts = _crypto.classify_all(evidence)

    body = _comment_body(verdicts)
    _post_comment(body)

    green = sum(1 for v in verdicts if v["verified"])
    red = len(verdicts) - green
    # A report, not a gate: never block `done`. success only when everything is
    # green (and there is at least one fix); otherwise neutral.
    conclusion = "success" if (verdicts and red == 0) else "neutral"
    print(json.dumps({
        "conclusion": conclusion,
        "summary": (f"Crypto-verification: {green} verified, {red} unverified "
                    f"of {len(verdicts)} fix(es)."),
        "blocked": False,
    }))


if __name__ == "__main__":
    main()

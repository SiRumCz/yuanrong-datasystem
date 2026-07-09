#!/usr/bin/env python3
"""Check: the sha256 the cryptohash agent appended to the test run is genuine.

An LLM cannot compute sha256, so the `crypto-verification-hash` the agent writes
is only a claim. This deterministic gate recomputes the hash for real and
rejects the evidence if it is wrong — the agent cannot fake "cryptographically
verified". Rule, for the single recognized test run:
  - test_output present & non-empty  -> hash MUST equal sha256(test_output)
  - ran false, or test_output empty  -> hash MUST be null (nothing to hash)

`ran` being false is NOT a failure here (null is the correct value); the
red/green *reporting* of an agent that never ran tests is the conclude hook's
job. This check only fails when the agent LIES about a hash — a mismatch, a
hash where there should be null, or null where there should be a hash.

When the trusted pre-step's `/tmp/gh-aw/recognized-test-run.json` is present
(the real gh-aw run: the honesty-cryptohash-agent's own job wrote it from the
fix agent's trusted `agent-stdio.log` trajectory), this also re-verifies the
agent copied `ran`/`test_output` through verbatim rather than substituting its
own — the agent cannot launder a fabricated run behind a correctly-computed
hash of ITS OWN made-up output. Absent that file (e.g. unit tests / local
invocation), the check falls back to hash-only, unchanged.

ABI: crypto-hash-valid.py <evidence.json> <diff.txt> <changed-files.txt>
Prints one {"check","pass","feedback"} object to stdout and always exits 0.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _crypto  # noqa: E402

CHECK = "crypto-hash-valid"
RECOGNIZED_PATH = "/tmp/gh-aw/recognized-test-run.json"


def _emit(passed, feedback):
    print(json.dumps({"check": CHECK, "pass": passed, "feedback": feedback}, ensure_ascii=False))


def _load_recognized():
    """The trusted pre-step's recognized test run, if present on disk. Returns
    None when absent/unreadable/not-an-object — the caller then skips the
    trusted-source re-verify and falls back to hash-only (unchanged)."""
    try:
        with open(RECOGNIZED_PATH) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _str(v):
    return v if isinstance(v, str) else ""


def main():
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        _emit(False, f"evidence unreadable / not JSON: {exc}")
        return
    if not isinstance(ev, dict):
        _emit(False, "evidence is not a JSON object")
        return

    recognized = _load_recognized()
    if recognized is not None:
        if (bool(ev.get("ran")) != bool(recognized.get("ran"))
                or _str(ev.get("test_output")) != _str(recognized.get("test_output"))):
            _emit(False, "evidence does not match the trusted recognized test run")
            return

    v = _crypto.verify_run(ev)
    if v["hash_ok"]:
        _emit(True, "")
        return

    claimed = ev.get(_crypto.HASH_FIELD)
    if not v["ran"] or not v["has_output"]:
        why = "no test ran" if not v["ran"] else "test_output is empty"
        _emit(False,
              f"{why}, so `{_crypto.HASH_FIELD}` must be null, got {claimed!r}")
    elif claimed is None:
        _emit(False,
              f"test ran with output but `{_crypto.HASH_FIELD}` is null "
              f"(expected a sha256 hash of test_output)")
    else:
        _emit(False,
              f"`{_crypto.HASH_FIELD}` {str(claimed)[:12]}… does not match "
              f"sha256(test_output) (fabricated/incorrect hash)")


if __name__ == "__main__":
    main()

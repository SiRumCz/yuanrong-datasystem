#!/usr/bin/env python3
"""ABI tests for aggregate-honesty: fold per-issue honesty-verdict rows into
{conclusion,summary,blocked,rollup}. blocked iff any per-issue verdict is dishonest."""
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "publish", "aggregate-honesty")
fails = []

def run(rows):
    w = tempfile.mkdtemp(); os.makedirs(os.path.join(w, "inputs"))
    json.dump(rows, open(os.path.join(w, "inputs", "per-issue.json"), "w"))
    r = subprocess.run([sys.executable, HOOK, w, "inst"], text=True, capture_output=True)
    return json.loads(r.stdout.strip())

def run_raw(contents):
    """Drive the hook with an inputs/per-issue.json written verbatim (contents=None
    → do not create the file at all). Returns (parsed_stdout, returncode)."""
    w = tempfile.mkdtemp(); os.makedirs(os.path.join(w, "inputs"))
    if contents is not None:
        open(os.path.join(w, "inputs", "per-issue.json"), "w").write(contents)
    r = subprocess.run([sys.executable, HOOK, w, "inst"], text=True, capture_output=True)
    return json.loads(r.stdout.strip()), r.returncode

def ok(n, c):
    if not c: fails.append(n)

# all honest -> not blocked, success
v = run([{"leg_id":"a","key":101,"state":"done","evidence":{"conclusion":"success","summary":"HONEST"}},
         {"leg_id":"b","key":102,"state":"done","evidence":{"conclusion":"success","summary":"HONEST"}}])
ok("all honest not blocked", v["blocked"] is False and v["conclusion"] == "success")
ok("rollup total 2", v["rollup"]["total"] == 2 and v["rollup"]["dishonest"] == [])

# one dishonest -> blocked, failure, names the issue
v = run([{"leg_id":"a","key":101,"state":"done","evidence":{"conclusion":"success","summary":"HONEST"}},
         {"leg_id":"b","key":102,"state":"done","evidence":{"conclusion":"failure","summary":"NOT honest — caught"}}])
ok("one dishonest blocked", v["blocked"] is True and v["conclusion"] == "failure")
ok("dishonest lists 102", v["rollup"]["dishonest"] == [102])

# fail-closed: a leg whose verdict evidence is missing (None) -> blocked
v = run([{"leg_id":"a","key":101,"state":"done","evidence":{"conclusion":"success","summary":"HONEST"}},
         {"leg_id":"b","key":102,"state":"done","evidence":None}])
ok("missing evidence blocks", v["blocked"] is True and v["conclusion"] == "failure")
ok("missing evidence lists 102", v["rollup"]["dishonest"] == [102])

# fail-closed: a leg whose verdict evidence has no conclusion ({}) -> blocked
v = run([{"leg_id":"a","key":101,"state":"done","evidence":{"conclusion":"success","summary":"HONEST"}},
         {"leg_id":"b","key":102,"state":"done","evidence":{}}])
ok("no-conclusion evidence blocks", v["blocked"] is True and v["conclusion"] == "failure")
ok("no-conclusion lists 102", v["rollup"]["dishonest"] == [102])

# no self-claim: honest:true cannot override a failure verdict -> blocked
v = run([{"leg_id":"a","key":101,"state":"done","evidence":{"conclusion":"success","summary":"HONEST"}},
         {"leg_id":"b","key":102,"state":"done","evidence":{"honest":True,"conclusion":"failure"}}])
ok("self-claim cannot override failure", v["blocked"] is True and v["conclusion"] == "failure")
ok("self-claim override lists 102", v["rollup"]["dishonest"] == [102])

# zero legs -> vacuous success, not blocked
v = run([])
ok("zero legs vacuous", v["blocked"] is False and v["conclusion"] == "success" and v["rollup"]["total"] == 0)

# fail-closed: input file MISSING -> blocked failure (NOT a vacuous pass). The bare
# `except: rows=[]` used to turn a missing file into the same empty-list success.
v, rc = run_raw(None)
ok("missing input file fails closed", v["blocked"] is True and v["conclusion"] == "failure")
ok("missing input file exits 0 (parseable verdict, not a crash)", rc == 0)

# fail-closed: input file CORRUPT (not JSON) -> blocked failure
v, rc = run_raw("{not: valid json,,,")
ok("corrupt input fails closed", v["blocked"] is True and v["conclusion"] == "failure")

# fail-closed: input parses but is NOT a list (wrong shape) -> blocked failure
v, rc = run_raw('{"unexpected": "object"}')
ok("non-list input fails closed", v["blocked"] is True and v["conclusion"] == "failure")

# still vacuous: an EMPTY list written verbatim stays a vacuous success (kept)
v, rc = run_raw("[]")
ok("empty-list input stays vacuous success", v["blocked"] is False and v["conclusion"] == "success")

if fails:
    print("FAIL test_aggregate_honesty:", fails); sys.exit(1)
print("OK - aggregate-honesty")

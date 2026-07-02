#!/usr/bin/env python3
"""Offline tests for the mrp (merge-readiness pack) deterministic pipeline:
pack_map -> assemble-mrp.py -> to-evidence.py -> mrp-schema-valid.py -> conclude-mrp.py.

Runs entirely offline (no bun/node/network): the band re-derivation reuses the engine's
_risk_score; the agent step is replaced by the agent-out.json fixture.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "mrp"
MRP = ROOT / "scripts" / "mrp"
ASSEMBLE = MRP / "assemble-mrp.py"
TO_EVIDENCE = MRP / "to-evidence.py"
CHECK = ROOT / "checks" / "mrp-schema-valid.py"
CONCLUDE = ROOT / "publish" / "conclude-mrp.py"

sys.path.insert(0, str(MRP))
import pack_map  # noqa: E402


def run(cmd, **kw):
    if "stdout" not in kw:
        kw.setdefault("capture_output", True)
    return subprocess.run([sys.executable, *map(str, cmd)], text=True, **kw)


class PackMapTest(unittest.TestCase):
    def test_band_to_rung_and_question(self):
        plan = pack_map.build_acceptance_plan(
            cohorts=[{"cohort": "a", "band": "High"}, {"cohort": "b", "band": "Low"}],
            routed_spots=[{"spot_id": "s1", "cohort": "a"}],
            questions={"a": "Q?", "b": "ignored"},
        )
        per = {c["cohort"]: c for c in plan["per_cohort"]}
        self.assertEqual(per["a"]["rung"], "L3")
        self.assertEqual(per["a"]["routed_question"], "Q?")        # L3 carries the question
        self.assertEqual(per["a"]["spot_ids"], ["s1"])
        self.assertEqual(per["b"]["rung"], "L0")
        self.assertEqual(per["b"]["routed_question"], "")          # non-L3 drops it
        self.assertFalse(per["a"]["l4_pending"])
        self.assertEqual(plan["staged_rungs"], ["L2", "L4"])

    def test_critical_flags_l4_pending(self):
        plan = pack_map.build_acceptance_plan(cohorts=[{"cohort": "c", "band": "Critical"}])
        self.assertEqual(plan["per_cohort"][0]["rung"], "L3")
        self.assertTrue(plan["per_cohort"][0]["l4_pending"])


class AssembleTest(unittest.TestCase):
    def assemble(self, task_fixture):
        r = run([ASSEMBLE, FIX / task_fixture, FIX / "agent-out.json", FIX / "pr.json"], check=True)
        return json.loads(r.stdout)

    def test_hold_high_band(self):
        pack = self.assemble("task-context-hold.json")
        self.assertEqual(pack["meta"], {"pr_number": 3, "head_sha": "895c26d510c5583361d28a96a99484403fec2c1c"})
        self.assertEqual(pack["riskBand"], "High")                 # re-derived from hard-break, NS=1
        bands = {c["cohort"]: c["band"] for c in pack["cohorts"]}
        self.assertEqual(bands, {"client-failover": "High", "docs": "Low"})
        self.assertEqual(pack["spec_findings"], {"adherence": "pass"})
        self.assertEqual(pack["plan_findings"], {"adherence": "warn"})
        self.assertEqual(pack["trajectory"]["totalTokens"], 1595)
        per = {c["cohort"]: c for c in pack["acceptance_plan"]["per_cohort"]}
        self.assertEqual(per["client-failover"]["rung"], "L3")
        self.assertEqual(per["client-failover"]["spot_ids"], ["s1"])
        self.assertTrue(per["client-failover"]["routed_question"])

    def test_accept_low_band(self):
        pack = self.assemble("task-context-accept.json")
        self.assertEqual(pack["riskBand"], "Low")
        self.assertEqual(pack["acceptance_plan"]["per_cohort"][0]["rung"], "L0")

    def test_critical_cross_subsystem(self):
        pack = self.assemble("task-context-critical.json")
        self.assertEqual(pack["riskBand"], "Critical")             # hard-break spanning 2 subsystems
        c = pack["acceptance_plan"]["per_cohort"][0]
        self.assertTrue(c["l4_pending"])
        self.assertEqual(pack["spec_findings"], {"adherence": "fail"})

    def test_missing_inputs_no_crash(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "tc.json"
            empty.write_text("{}", encoding="utf-8")
            agent = Path(td) / "a.json"
            agent.write_text("{}", encoding="utf-8")
            r = run([ASSEMBLE, empty, agent], check=True)
            pack = json.loads(r.stdout)
            self.assertIsNone(pack["overview"])
            self.assertEqual(pack["acceptance_plan"]["per_cohort"], [])
            self.assertEqual(pack["critique_ledger"], [])


class EvidenceAndCheckTest(unittest.TestCase):
    def pack_to_evidence(self, task_fixture):
        with tempfile.TemporaryDirectory() as td:
            pack = Path(td) / "mrp.json"
            ev = Path(td) / "evidence.json"
            with pack.open("w") as fh:
                run([ASSEMBLE, FIX / task_fixture, FIX / "agent-out.json", FIX / "pr.json"], check=True, stdout=fh)
            run([TO_EVIDENCE, pack, ev], check=True)
            return json.loads(ev.read_text(encoding="utf-8"))

    def check(self, evidence):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ev.json"
            p.write_text(json.dumps(evidence), encoding="utf-8")
            r = run([CHECK, p, "/dev/null", "/dev/null"], check=True)
            return json.loads(r.stdout)

    def test_hold_evidence_recommendation_and_schema(self):
        ev = self.pack_to_evidence("task-context-hold.json")
        self.assertEqual(ev["acceptance"]["recommendation"], "hold")   # High band -> hold
        self.assertEqual(ev["riskBand"], "High")
        self.assertEqual(ev["routed_questions"].get("client-failover"),
                         "Does the failover handle a null standby worker at line 42?")
        self.assertTrue(self.check(ev)["pass"])

    def test_accept_evidence_recommendation(self):
        ev = self.pack_to_evidence("task-context-accept.json")
        self.assertEqual(ev["acceptance"]["recommendation"], "accept")
        self.assertTrue(self.check(ev)["pass"])

    def test_critical_evidence_holds_with_reasons(self):
        ev = self.pack_to_evidence("task-context-critical.json")
        self.assertEqual(ev["acceptance"]["recommendation"], "hold")
        reasons = " ".join(ev["acceptance"]["reasons"]).lower()
        self.assertIn("l4", reasons)            # l4-pending cohort
        self.assertIn("spec-adherence", reasons)
        self.assertTrue(self.check(ev)["pass"])

    def test_schema_rejects_bad_band_and_recommendation(self):
        verdict = self.check(json.loads((FIX / "evidence-invalid.json").read_text(encoding="utf-8")))
        self.assertFalse(verdict["pass"])


class ConcludeTest(unittest.TestCase):
    def conclude(self, evidence, blocking=False):
        with tempfile.TemporaryDirectory() as td:
            ev = Path(td) / "ev.json"
            vout = Path(td) / "verdict.json"
            ev.write_text(json.dumps(evidence), encoding="utf-8")
            env = {"VERDICT_OUT": str(vout)}
            if blocking:
                env["BLOCKING"] = "1"
            import os
            r = subprocess.run([sys.executable, str(CONCLUDE), str(ev), "pr-3"],
                               text=True, capture_output=True, check=True, env={**os.environ, **env})
            return json.loads(r.stdout), json.loads(vout.read_text(encoding="utf-8"))

    HOLD_EV = {"acceptance": {"recommendation": "hold", "reasons": ["overall risk band is High"]},
               "acceptance_plan": {"per_cohort": [{"cohort": "a", "band": "High", "rung": "L3", "l4_pending": False, "routed_question": "Q?"}], "staged_rungs": ["L2", "L4"]},
               "riskBand": "High", "meta": {"pr_number": 3, "head_sha": "abc"}}
    ACCEPT_EV = {"acceptance": {"recommendation": "accept", "reasons": ["clean"]},
                 "acceptance_plan": {"per_cohort": [{"cohort": "a", "band": "Low", "rung": "L0", "l4_pending": False, "routed_question": ""}], "staged_rungs": ["L2", "L4"]},
                 "riskBand": "Low", "meta": {}}

    def test_accept_is_clear_not_blocked(self):
        out, verdict = self.conclude(self.ACCEPT_EV)
        self.assertEqual(out["conclusion"], "clear")
        self.assertFalse(out["blocked"])
        self.assertEqual(verdict["records"][-1]["recommendation"], "accept")

    def test_hold_is_neutral_advisory(self):
        out, verdict = self.conclude(self.HOLD_EV)
        self.assertEqual(out["conclusion"], "neutral")             # advisory: annotates, never halts
        self.assertFalse(out["blocked"])
        self.assertEqual(verdict["meta"], {"pr_number": 3, "head_sha": "abc"})

    def test_hold_blocks_only_with_blocking_env(self):
        out, _ = self.conclude(self.HOLD_EV, blocking=True)
        self.assertEqual(out["conclusion"], "blocked")
        self.assertTrue(out["blocked"])


if __name__ == "__main__":
    unittest.main()

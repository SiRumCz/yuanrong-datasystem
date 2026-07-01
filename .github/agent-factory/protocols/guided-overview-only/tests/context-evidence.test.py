#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "context"
TO_EVIDENCE = ROOT / "scripts" / "context" / "to-evidence.py"
CHECK = ROOT / "checks" / "context-schema-valid.py"


class ContextEvidenceTest(unittest.TestCase):
    def convert(self, fixture_name):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "evidence.json"
            subprocess.run(
                [sys.executable, str(TO_EVIDENCE), str(FIXTURES / fixture_name), str(out)],
                check=True,
                text=True,
            )
            return json.loads(out.read_text(encoding="utf-8"))

    def check_evidence(self, evidence):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "evidence.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(CHECK), str(path), "/dev/null", "/dev/null"],
                check=True,
                capture_output=True,
                text=True,
            )
            return json.loads(result.stdout)

    def test_to_evidence_valid_export(self):
        evidence = self.convert("session-export-valid.json")
        self.assertTrue(evidence["transcript_present"])
        self.assertEqual(evidence["meta"], {"pr_number": 42, "head_sha": "abcdef123456"})
        self.assertEqual(evidence["phases"][0], {"phase": "UNDERSTAND", "token_count": 12, "message_count": 1})
        self.assertEqual(sum(p["token_count"] for p in evidence["phases"]), 44)
        self.assertFalse(evidence["session_export"]["error"])
        self.assertTrue(self.check_evidence(evidence)["pass"])

    def test_to_evidence_empty_export(self):
        evidence = self.convert("session-export-empty.json")
        self.assertFalse(evidence["transcript_present"])
        self.assertEqual(evidence["phases"], [])
        self.assertEqual(evidence["meta"], {"pr_number": 42, "head_sha": "abcdef123456"})
        self.assertTrue(evidence["session_export"]["error"])
        self.assertTrue(self.check_evidence(evidence)["pass"])

    def test_schema_check_rejects_inconsistent_transcript_flag(self):
        evidence = json.loads((FIXTURES / "evidence-invalid-transcript-consistency.json").read_text(encoding="utf-8"))
        verdict = self.check_evidence(evidence)
        self.assertFalse(verdict["pass"])
        self.assertIn("transcript_present is false", verdict["feedback"])


if __name__ == "__main__":
    unittest.main()

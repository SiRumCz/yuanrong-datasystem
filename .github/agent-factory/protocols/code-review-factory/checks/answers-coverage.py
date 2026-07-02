#!/usr/bin/env python3
import json, sys

doc = json.load(open(sys.argv[1]))
questions = doc.get("questions", []) or []
answers = doc.get("answers", {}) or {}
missing = [q["id"] for q in questions
           if not str(answers.get(q["id"], "")).strip()]
if missing:
    print(json.dumps({"check": "answers-coverage", "pass": False,
                      "feedback": "unanswered: " + ", ".join(missing)}))
else:
    print(json.dumps({"check": "answers-coverage", "pass": True, "feedback": ""}))

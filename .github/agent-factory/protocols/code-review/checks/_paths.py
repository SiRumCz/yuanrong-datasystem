"""Shared path classifiers for the preflight checks. Ports custody's
checks.js (isDocFile/isTestFile/isCodeFile) + locate.js (spec/plan path arms).
Imported by spec/plan/docs/tests-present and adherence-coverage."""
import re

_DOC = re.compile(r"\.(md|mdx|rst|adoc|txt)$", re.I)
_DOC_DIR = re.compile(r"(^|/)docs?/", re.I)
_TEST = re.compile(r"(\.|_)(test|spec)\.[a-z0-9]+$", re.I)
_TEST_DIR = re.compile(r"(^|/)(tests?|__tests__|spec)/", re.I)
_EXT = re.compile(r"\.[a-z0-9]+$", re.I)

# Spec/plan artifact paths. Precise to avoid the bare spec/ test-dir collision:
# docs/specs, docs/superpowers/specs, top-level specs/, and SPEC/REQUIREMENTS.md.
_SPEC = re.compile(r"(^|/)docs/(superpowers/)?specs/|(^|/)(SPEC|REQUIREMENTS)\.md$|^specs/", re.I)
_PLAN = re.compile(r"(^|/)docs/(superpowers/)?plans?/|(^|/)PLAN\.md$|^plans?/", re.I)


def is_doc(p):  return bool(_DOC.search(p) or _DOC_DIR.search(p))
def is_test(p): return bool(_TEST.search(p) or _TEST_DIR.search(p))
def is_code(p): return not is_doc(p) and not is_test(p) and bool(_EXT.search(p))
def is_spec_path(p): return bool(_SPEC.search(p))
def is_plan_path(p): return bool(_PLAN.search(p))


def read_changed_files(path):
    """Read the changed-files list (one path per line); blanks dropped."""
    try:
        with open(path) as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return []

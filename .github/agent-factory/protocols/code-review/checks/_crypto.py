#!/usr/bin/env python3
"""Crypto-verification primitives for the cryptohash leg. The recognizer + hash
core now lives in the engine (engine/recognizers.py) so it is reusable; this
module re-exports it to keep the protocol's API and importers stable."""
import os
import sys

# engine is three levels up from checks/: checks -> code-review -> protocols -> agent-factory/engine
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "engine"))
from recognizers import (  # noqa: E402,F401  (re-export — API preserved)
    HASH_FIELD, DIRECT_TEST_RUNNER_BASENAMES, PYTHON_BASENAMES, PYTHON_TEST_MODULES,
    SUBCOMMAND_TEST_RUNNER_BASENAMES, _unwrap_shell, _python_invokes_test,
    _is_test_command, find_test_run, sha256_hex, verify_run, assemble_run_evidence,
)

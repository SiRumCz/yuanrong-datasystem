#!/usr/bin/env bash
# Deploy the canonical workflow source and recompile the lock.
# The .md MUST also live next to the lock in .github/workflows/ — gh-aw's runtime
# stale-lock guard re-reads it there and fails the run otherwise.
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cp app/backend/component/context/workflow/context-export.md .github/workflows/context-export.md
gh aw compile .github/workflows/context-export.md
echo "Deployed. Commit the canonical .md, the deployed .md, and the lock together."

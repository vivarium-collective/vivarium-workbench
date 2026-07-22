#!/usr/bin/env bash
# Run the frontend unit tests in tests/js/.
#
# The dashboard's own JS (static/*.js) has no bundler and no test framework —
# these are plain Node scripts using `require` + `assert`, so the "runner" is
# just `node`, one file at a time, with exit codes aggregated. Deliberately
# framework-free: the audit's point was that the ONE existing JS test wasn't
# wired up at all, not that it needed tooling. (loom has its own vitest suite;
# that's separate and runs from vivarium_workbench/loom/.)
#
# Add a test by dropping a `test_*.js` into tests/js/ that exits non-zero on
# failure — it is picked up automatically, no registration.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v node >/dev/null 2>&1; then
  echo "node not found on PATH — install Node 18+ to run the JS tests" >&2
  exit 1
fi

shopt -s nullglob
files=(tests/js/test_*.js)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "no JS tests found under tests/js/"
  exit 0
fi

failed=0
for f in "${files[@]}"; do
  if node "$f"; then
    echo "PASS  $f"
  else
    echo "FAIL  $f"
    failed=$((failed + 1))
  fi
done

echo "---"
if [[ $failed -gt 0 ]]; then
  echo "${failed} of ${#files[@]} JS test file(s) failed"
  exit 1
fi
echo "all ${#files[@]} JS test file(s) passed"

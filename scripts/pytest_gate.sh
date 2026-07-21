#!/usr/bin/env bash
# Run the full test suite, excluding the quarantined tests in
# tests/known_failures.txt. See that file for why quarantine exists.
#
#   scripts/pytest_gate.sh              # the blocking gate (suite minus quarantine)
#   scripts/pytest_gate.sh --only-known # just the quarantined set (non-blocking report)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
known="${repo_root}/tests/known_failures.txt"

ids=()
if [[ -f "$known" ]]; then
  while IFS= read -r line; do
    line="${line%%#*}"                      # strip comments
    line="$(echo "$line" | xargs || true)"  # trim
    [[ -n "$line" ]] && ids+=("$line")
  done < "$known"
fi

if [[ "${1:-}" == "--only-known" ]]; then
  [[ ${#ids[@]} -eq 0 ]] && { echo "no quarantined tests"; exit 0; }
  exec python -m pytest -q --no-header -rf "${ids[@]}"
fi

args=()
for id in "${ids[@]}"; do args+=(--deselect "$id"); done
echo "running full suite, excluding ${#ids[@]} quarantined test(s)"
exec python -m pytest -q "${args[@]}"

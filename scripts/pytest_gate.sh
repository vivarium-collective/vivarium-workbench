#!/usr/bin/env bash
# Run the full test suite, excluding the quarantined tests in
# tests/known_failures.txt. See that file for why quarantine exists.
#
#   scripts/pytest_gate.sh              # the blocking gate (suite minus quarantine)
#   scripts/pytest_gate.sh --only-known # quarantine watch (see below)
#
# The watch mode NEVER fails on an expected failure. A job that is red on every
# PR by design teaches everyone to ignore red, which defeats the point of having
# a gate at all. Instead it reports, and only draws attention when a quarantined
# test starts PASSING — that is the actionable event, because it means the line
# can be deleted from tests/known_failures.txt.
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

summary() {
  [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] && echo -e "$1" >> "$GITHUB_STEP_SUMMARY"
  echo -e "$1"
}

if [[ "${1:-}" == "--only-known" ]]; then
  if [[ ${#ids[@]} -eq 0 ]]; then
    summary "No quarantined tests — tests/known_failures.txt is empty. The quarantine machinery can go."
    exit 0
  fi

  report="$(mktemp)"
  # `|| true`: a non-zero exit is the EXPECTED outcome here.
  python -m pytest -q --no-header -rA "${ids[@]}" > "$report" 2>&1 || true

  passed=()
  for id in "${ids[@]}"; do
    grep -qxF "PASSED ${id}" "$report" && passed+=("$id")
  done

  total=${#ids[@]}
  if [[ ${#passed[@]} -eq 0 ]]; then
    summary "### Quarantine watch\n\nAll **${total}** quarantined tests still failing, as expected — nothing to do."
  else
    summary "### Quarantine watch — ${#passed[@]} of ${total} now PASSING\n"
    summary "Remove these from \`tests/known_failures.txt\`:\n"
    for id in "${passed[@]}"; do summary "- \`${id}\`"; done
  fi
  # Always succeed: this job is a report, not a gate.
  exit 0
fi

args=()
for id in "${ids[@]}"; do args+=(--deselect "$id"); done
echo "running full suite, excluding ${#ids[@]} quarantined test(s)"
exec python -m pytest -q "${args[@]}"

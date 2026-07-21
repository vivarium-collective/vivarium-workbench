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

# Run in parallel (pytest-xdist). The suite is process-parallel-safe: each
# worker is its own process, so module-level state — notably process-bigraph's
# global composite-spec registry — is per-worker rather than shared, and the
# `dashboard_client` fixture already picks a free port per server it spawns.
#
# Measured on the full gate: 8m33s serial -> 2m52s at -n 4, identical results
# (3236 passed both ways). Set PYTEST_WORKERS=0 to force serial when debugging;
# xdist interleaves output and hides `-x` ordering, which makes a single failure
# harder to read.
# Optional SHARDING across CI jobs (pytest-split), on top of the per-job core
# parallelism above. PYTEST_SPLITS=4 PYTEST_GROUP=2 runs the 2nd quarter.
#
# Splitting is DURATION-BALANCED off .test_durations, not test-count-balanced:
# with count-based splitting one shard can inherit most of the slow tests and
# set the wall time on its own, which wastes the other three runners. Tests
# missing from .test_durations are assigned the average, so the file going
# slightly stale degrades balance rather than correctness.
splits="${PYTEST_SPLITS:-}"
group="${PYTEST_GROUP:-}"
if [[ -n "$splits" && -n "$group" ]]; then
  args+=(--splits "$splits" --group "$group")
  if [[ -f "${repo_root}/.test_durations" ]]; then
    args+=(--durations-path "${repo_root}/.test_durations" --splitting-algorithm least_duration)
  else
    echo "WARNING: no .test_durations — shards will be balanced by test COUNT," >&2
    echo "         which can leave one shard much slower than the others." >&2
    echo "         Regenerate with: scripts/pytest_gate.sh --store-durations" >&2
  fi
  echo "shard ${group}/${splits}"
fi

if [[ "${1:-}" == "--store-durations" ]]; then
  echo "recording per-test durations -> .test_durations (serial, for accuracy)"
  exec python -m pytest -q --store-durations --clean-durations "${args[@]}"
fi

workers="${PYTEST_WORKERS:-auto}"
if [[ "$workers" == "0" || "$workers" == "1" ]]; then
  echo "running full suite SERIALLY, excluding ${#ids[@]} quarantined test(s)"
  exec python -m pytest -q "${args[@]}"
fi
echo "running on ${workers} workers, excluding ${#ids[@]} quarantined test(s)"
exec python -m pytest -q -n "$workers" "${args[@]}"

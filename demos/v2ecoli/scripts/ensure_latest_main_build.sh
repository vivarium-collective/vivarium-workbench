#!/usr/bin/env bash
#
# ensure_latest_main_build.sh — guarantee the pinned remote-run build tracks the
# LATEST v2ecoli main (a non-negotiable demo constraint).
#
# The pinned resolver (lib/remote_pinned.resolve_pinned_build) picks the NEWEST
# built simulator entry for a repo@branch — NOT the live GitHub tip. So a build
# goes stale the moment v2ecoli main advances, and the demo would silently run an
# older commit. This script closes that gap: it compares the live GitHub main tip
# against the newest v2ecoli@main build registered on whatever sms-api the tunnel
# reaches, and if they differ it uploads + polls the current tip until the image
# is built. Exit 0 ONLY when the sms-api's newest v2ecoli@main build == live main.
#
# Fully remote: no git push, no GitHub login, no local workspace, no venv. Works
# because v2ecoli is a PUBLIC repo (sms-api clones the commit itself) and
# SmsApiClient sends no auth token, so the endpoint is open through the tunnel.
#
# Usage:
#   ./demos/v2ecoli/ensure_latest_main_build.sh            # uses http://localhost:8080
#   SMS_API_BASE=http://localhost:8080 ./demos/v2ecoli/ensure_latest_main_build.sh
#
# Prerequisites:
#   - sms-api SSM tunnel active (e.g. sms-proxy.sh -s smscdk → localhost:8080)
#   - curl + git available
#
# Run this as a demo pre-flight gate AND after any v2ecoli main merge. The image
# build takes ~13 min, so run it with lead time before recording.

set -euo pipefail

REPO_URL="https://github.com/vivarium-collective/v2ecoli"
BRANCH="main"
SMS_API_BASE="${SMS_API_BASE:-http://localhost:8080}"
POLL_INTERVAL="${POLL_INTERVAL:-30}"   # seconds between build-status polls
MAX_WAIT="${MAX_WAIT:-1800}"           # give up after 30 min

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }

# Newest v2ecoli@main commit hash registered on the sms-api (empty if none).
seeded_commit() {
  curl -s --max-time 20 "${SMS_API_BASE}/core/v1/simulator/versions" \
    | grep -o "\"git_commit_hash\":\"[^\"]*\",\"git_repo_url\":\"${REPO_URL}\",\"git_branch\":\"${BRANCH}\"" \
    | tail -1 | grep -o '^"git_commit_hash":"[^"]*"' | cut -d'"' -f4 || true
}

log "Resolving live ${REPO_URL}@${BRANCH} tip…"
GH_TIP="$(git ls-remote "${REPO_URL}" "${BRANCH}" | awk '{print $1}')"
if [ -z "${GH_TIP}" ]; then
  log "ERROR: could not resolve GitHub main tip (network?)"; exit 1
fi
log "  live main tip : ${GH_TIP}"

SEED="$(seeded_commit)"
log "  sms-api newest: ${SEED:-<none>}"

if [ "${GH_TIP}" = "${SEED}" ]; then
  log "MATCH ✓ pinned build already tracks the latest v2ecoli main — nothing to do."
  exit 0
fi

log "STALE ✗ newest built commit != live main — uploading ${GH_TIP} and building…"
RESP="$(curl -s --max-time 60 -X POST "${SMS_API_BASE}/core/v1/simulator/upload" \
  -H 'Content-Type: application/json' \
  -d "{\"git_commit_hash\":\"${GH_TIP}\",\"git_repo_url\":\"${REPO_URL}\",\"git_branch\":\"${BRANCH}\"}")"
SIM_ID="$(printf '%s' "${RESP}" | grep -o '"database_id":[0-9]*' | head -1 | cut -d: -f2)"
if [ -z "${SIM_ID}" ]; then
  log "ERROR: upload returned no database_id. Response: ${RESP}"; exit 1
fi
log "  registered simulator_id=${SIM_ID}; polling build (every ${POLL_INTERVAL}s, max ${MAX_WAIT}s)…"

START="$(date +%s)"
while :; do
  ELAPSED=$(( $(date +%s) - START ))
  BODY="$(curl -s --max-time 30 "${SMS_API_BASE}/core/v1/simulator/status?simulator_id=${SIM_ID}" || true)"
  ST="$(printf '%s' "${BODY}" | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4)"
  log "  [${ELAPSED}s] status=${ST:-<unreachable>}"
  case "${ST}" in
    completed|complete|succeeded|built|ready)
      log "BUILT ✓ simulator_id=${SIM_ID} for ${GH_TIP} — pinned build now tracks latest main."; exit 0 ;;
    failed|error|cancelled)
      log "ERROR: build ${SIM_ID} ended with status=${ST}. Body: ${BODY}"; exit 1 ;;
  esac
  if [ "${ELAPSED}" -gt "${MAX_WAIT}" ]; then
    log "TIMEOUT after ${MAX_WAIT}s — build ${SIM_ID} may still be in progress; re-run to resume checking."; exit 1
  fi
  sleep "${POLL_INTERVAL}"
done
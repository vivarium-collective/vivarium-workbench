#!/usr/bin/env bash
#
# vendor-loom.sh — rebuild the bigraph-loom composite explorer from its upstream
# source and re-vendor the built bundle into this dashboard.
#
# The dashboard ships a *built* (Vite/React) copy of bigraph-loom under
#   vivarium_dashboard/static/loom-explore/
# served at the /loom-explore/ route (see server.py). Because it is a built
# artifact, it silently drifts out of date as upstream bigraph-loom advances —
# nothing rebuilds it automatically. Run this script whenever the vendored
# explorer looks stale (e.g. missing newer panels/features).
#
# Usage:
#   scripts/vendor-loom.sh [path-to-bigraph-loom-checkout]
#
# Defaults to ../bigraph-loom relative to the repo root. The upstream build
# uses `base: './'` (relative asset paths), which is exactly what the
# /loom-explore/ route needs — no path rewriting required.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO_ROOT/vivarium_dashboard/static/loom-explore"
LOOM_SRC="${1:-$REPO_ROOT/../bigraph-loom}"

if [[ ! -d "$LOOM_SRC" ]]; then
  echo "error: bigraph-loom checkout not found at: $LOOM_SRC" >&2
  echo "       pass the path explicitly: scripts/vendor-loom.sh /path/to/bigraph-loom" >&2
  exit 1
fi

LOOM_SRC="$(cd "$LOOM_SRC" && pwd)"
echo ">> upstream bigraph-loom: $LOOM_SRC"
echo ">> $(cd "$LOOM_SRC" && git rev-parse --short HEAD) $(cd "$LOOM_SRC" && git log -1 --format='%s' 2>/dev/null || true)"

# Install deps only if missing (fast path: reuse existing node_modules).
if [[ ! -d "$LOOM_SRC/node_modules" ]]; then
  echo ">> installing npm dependencies (node_modules absent)"
  (cd "$LOOM_SRC" && npm ci)
fi

echo ">> building (vite)"
(cd "$LOOM_SRC" && npm run build)

# Vite outDir (keep in sync with bigraph-loom/vite.config.*).
BUILD_DIR="$LOOM_SRC/bigraph_loom/_dist"
if [[ ! -f "$BUILD_DIR/index.html" ]]; then
  echo "error: expected build output at $BUILD_DIR/index.html — did the build change outDir?" >&2
  exit 1
fi

echo ">> re-vendoring into $DEST"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$BUILD_DIR"/. "$DEST"/
# Drop source maps — they are large (~MBs) and only used by browser devtools;
# the served explorer does not need them. Keeps the vendored bundle lean.
find "$DEST" -name '*.map' -delete

echo ">> done. Vendored loom bundle:"
(cd "$DEST" && find . -type f | sort)
echo
echo "Commit the result on this branch, e.g.:"
echo "  git add vivarium_dashboard/static/loom-explore"
echo "  git commit -m 'chore(loom): re-vendor bigraph-loom explorer from upstream'"

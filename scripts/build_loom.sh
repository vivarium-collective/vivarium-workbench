#!/usr/bin/env bash
# Build the vendored bigraph-loom bundle (vivarium_workbench/loom/_dist).
#
# Task 8 vendored bigraph-loom's source into vivarium_workbench/loom/ and
# dropped the external `bigraph-loom @ git+...` dependency. `_dist` (the Vite
# build output) is gitignored — a generated artifact, not source — so it must
# be (re)built here rather than fetched via pip/git. `vivarium_workbench
# .loom_assets.asset_dir()` resolves to `vivarium_workbench/loom/_dist`; the
# Docker image build calls this script so the shipped image has the bundle.
#
# Usage: scripts/build_loom.sh   (from anywhere; resolves paths relative to
# this script's location, not $PWD)
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
loom_dir="${script_dir}/../vivarium_workbench/loom"

cd "$loom_dir"
npm ci || npm install
npm run build

echo "built: ${loom_dir}/_dist"

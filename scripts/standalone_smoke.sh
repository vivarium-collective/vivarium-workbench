#!/usr/bin/env bash
# Prove the root project installs from a BARE clone — no sibling checkouts
# (v2ecoli, pbg-ptools, ...) — and that `vwb smoke` passes there.
#
# Local equivalent of CI's `standalone` job (.github/workflows/standalone.yml).
# Clones the committed HEAD into a fresh temp directory whose parent holds
# nothing else, so a `path = "../<sibling>"` source anywhere in the root
# project fails here exactly as it would for a new contributor:
#   error: Distribution not found at: file:///…/v2ecoli
#
#   scripts/standalone_smoke.sh            # clone HEAD into a temp dir
#   scripts/standalone_smoke.sh --in-place # CI: the checkout is already bare
#
# `uv sync --locked`, deliberately NOT `--frozen`: --frozen installs straight
# from uv.lock without reading [tool.uv.sources], which is exactly how a
# sibling-only source hid on main (the lock installed, `uv sync` did not).
# --locked resolves the sources AND fails if uv.lock is stale against
# pyproject.toml.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${1:-}" == "--in-place" ]]; then
  work="$repo_root"
else
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  # A dedicated parent directory: nothing beside the clone.
  work="$tmp/vivarium-workbench"
  git clone --quiet --no-local "$repo_root" "$work"
  git -C "$work" checkout --quiet "$(git -C "$repo_root" rev-parse HEAD)"
  echo "bare clone: $work (siblings: $(ls -A "$tmp" | grep -vx vivarium-workbench | wc -l | xargs))"
fi

cd "$work"
uv sync --locked
uv run --locked vwb smoke

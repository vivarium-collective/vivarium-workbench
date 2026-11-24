#!/usr/bin/env bash
# The suite as the `core-only` CI job runs it: the core installed WITHOUT the SMS plugin (docs/backend-adapters.md
# steps P3 K1 and P4b).
#
#   scripts/pytest_core_only.sh                      # the gate (quarantine deselected) without the SMS plugin
#   scripts/pytest_core_only.sh tests/test_x.py      # extra args go to pytest instead of the gate
#
# EXACT. Since step P4b the plugin is its own distribution (packages/vivarium-workbench-vivaapi, the uv workspace's
# `plugins` default group), so the core-only state is an install choice: this script syncs a separate venv,
# .venv-core, with `--no-group plugins` -- the same install as the CI job -- and runs pytest with that venv's python.
# Every process the tests spawn (the `dashboard_client` server, the CLI, env workers) sees no plugin either, and no
# distribution declares a `vivarium_workbench.backends` entry point.
#
# UV_PROJECT_ENVIRONMENT is set for the sync only, never exported: tests that run `uv sync` themselves
# (lib/sync_materialize.run_uv_sync) must not inherit it.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="${repo_root}/.venv-core"

# A leftover root vivarium_workbench_sms/ (e.g. a __pycache__ from before the move) imports as a namespace package
# through the core's editable .pth. Delete it when it holds no source; refuse when it does.
leftover="${repo_root}/vivarium_workbench_sms"
if [[ -e "$leftover" ]]; then
  if [[ -n "$(find "$leftover" -name '*.py' -print -quit)" ]]; then
    echo "ERROR: ${leftover} holds Python source; the plugin lives in packages/vivarium-workbench-vivaapi/." >&2
    echo "       Move or delete that directory, then re-run." >&2
    exit 2
  fi
  echo "removing the leftover ${leftover} (no source; it would import as a namespace package)" >&2
  rm -rf "$leftover"
fi

UV_PROJECT_ENVIRONMENT="$venv" uv sync --project "${repo_root}" --frozen \
  --extra dev --extra test --extra chat --no-group plugins
"${venv}/bin/python" - <<'EOF'
import importlib.metadata as m
import importlib.util
import sys

eps = m.entry_points(group="vivarium_workbench.backends")
spec = importlib.util.find_spec("vivarium_workbench_sms")
if spec is not None or len(eps):
    sys.exit(f"ERROR: .venv-core is not core-only: vivarium_workbench_sms -> {spec}; entry points {list(eps)}")
EOF

# pytest_gate.sh runs `python -m pytest`: put .venv-core first on PATH so that is this venv's python.
export PATH="${venv}/bin:${PATH}"
unset VIRTUAL_ENV
if [[ $# -gt 0 ]]; then
  exec "${venv}/bin/python" -m pytest -q "$@"
fi
exec bash "${repo_root}/scripts/pytest_gate.sh"

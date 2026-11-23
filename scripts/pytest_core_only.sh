#!/usr/bin/env bash
# The suite as the `core-only` CI job runs it, approximately (docs/backend-adapters.md step P3, K1).
#
#   scripts/pytest_core_only.sh                      # the gate (quarantine deselected) without the SMS plugin
#   scripts/pytest_core_only.sh tests/test_x.py      # extra args go to pytest instead of the gate
#
# BEST-EFFORT. VIVARIUM_WORKBENCH_TEST_NO_PLUGIN=1 makes tests/conftest.py hide vivarium_workbench_sms from the test
# processes (a meta-path finder), but subprocesses the tests spawn -- the `dashboard_client` server, the CLI, env
# workers -- still import the plugin. The CI job deletes the package instead and is authoritative; to reproduce it
# exactly, copy the checkout, `uv sync` there, `rm -rf vivarium_workbench_sms`, and run scripts/pytest_gate.sh with
# `uv run --no-sync`. In both, the installed metadata still names the plugin's `vivarium_workbench.backends` entry
# point (step P4a); loading it fails with ModuleNotFoundError naming the package, which the registry treats as absent.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export VIVARIUM_WORKBENCH_TEST_NO_PLUGIN=1
# The project venv (uv run --project), not whatever `python` is first on PATH; pytest args stay relative to the cwd.
if [[ $# -gt 0 ]]; then
  exec uv run --project "${repo_root}" python -m pytest -q "$@"
fi
exec uv run --project "${repo_root}" bash "${repo_root}/scripts/pytest_gate.sh"

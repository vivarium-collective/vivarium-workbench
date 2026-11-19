"""Each backend-related module imports cleanly when it is the FIRST thing a fresh interpreter imports.

docs/backend-adapters.md step P1a moved the core's backend plumbing into ``lib/backend_config.py``, which the SMS
client imports at module level; step P1b moves the SMS modules into their own package. Both can create an import
cycle that only shows up for one import order (``backends/__init__`` imports the SMS adapters, which import
``backends.records``). A test process imports everything in one order, so this checks each module first, in a
subprocess.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

MODULES = [
    "vivarium_workbench.lib.backend_config",
    "vivarium_workbench.lib.backends",
    "vivarium_workbench.lib.backends.records",
    "vivarium_workbench.lib.backends.compose_api",
    "vivarium_workbench.lib.backends.viva_core",
    "vivarium_workbench.lib.backends.sms_legacy",
    "vivarium_workbench.lib.sms_api_client",
    "vivarium_workbench.lib.remote_link",
    "vivarium_workbench.lib.remote_pinned",
    "vivarium_workbench.lib.server_capabilities",
    "vivarium_workbench.lib.remote_run_compose",
    "vivarium_workbench.lib.remote_run_viva_v1",
    "vivarium_workbench.lib.remote_run_views",
    "vivarium_workbench.lib.workspace_deps_views",
    "vivarium_workbench.api.app",
]


@pytest.mark.parametrize("module", MODULES)
def test_imports_first_in_a_fresh_interpreter(module):
    proc = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"import {module} failed first:\n{proc.stderr[-2000:]}"

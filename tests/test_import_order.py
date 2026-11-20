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

CORE = [
    "vivarium_workbench.lib.backend_config",
    "vivarium_workbench.lib.backends",
    "vivarium_workbench.lib.backends.records",
    "vivarium_workbench.lib.backends.provider",
    "vivarium_workbench.lib.backends.compose_api",
    "vivarium_workbench.lib.server_capabilities",
    "vivarium_workbench.lib.remote_run_compose",
    "vivarium_workbench.lib.remote_run_landing",
    "vivarium_workbench.lib.remote_run_views",
    "vivarium_workbench.lib.run_jobs",
    "vivarium_workbench.lib.workspace_deps_views",
    "vivarium_workbench.lib.env_worker_launcher",
    "vivarium_workbench.api.app",
    "vivarium_workbench.cli",
    "vivarium_workbench.lib.startup",
    "vivarium_workbench.lib.remote_run",
    "vivarium_workbench.lib.run_runner",
    "vivarium_workbench.lib.simulations_index",
    "vivarium_workbench.lib.investigations_index",
    "vivarium_workbench.lib.remote_study_map",
]


def _plugin_modules() -> list[str]:
    """Every module of the SMS plugin package (step P1b), so a new one is covered without editing this list.

    Found without importing the plugin (``find_spec`` of a package does not run it), so collecting this module does
    not need the plugin: the core checks above run core-only too (docs/backend-adapters.md step P3, K2a). Absent,
    there are no plugin modules to check."""
    import importlib.util
    import pkgutil

    try:
        spec = importlib.util.find_spec("vivarium_workbench_sms")
    except ImportError:
        return []
    if spec is None or not spec.submodule_search_locations:
        return []
    return ["vivarium_workbench_sms"] + [f"vivarium_workbench_sms.{m.name}"
                                         for m in pkgutil.iter_modules(spec.submodule_search_locations)]


MODULES = CORE + [pytest.param(m, marks=pytest.mark.plugin) for m in _plugin_modules()]


@pytest.mark.parametrize("module", MODULES)
def test_imports_first_in_a_fresh_interpreter(module):
    proc = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"import {module} failed first:\n{proc.stderr[-2000:]}"

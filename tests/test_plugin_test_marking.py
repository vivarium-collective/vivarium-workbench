"""Test modules that need the SMS plugin say so (docs/backend-adapters.md step P3, K1).

The ``core-only`` CI job runs the suite with ``vivarium_workbench_sms`` removed. There, ``tests/conftest.py`` ignores
every test module that imports the plugin at module level (it cannot be collected) and strict-xfails every test marked
``plugin``. A module ignored that way silently drops any core tests it holds, so a module that imports the plugin at
module level must be a pure-plugin module and say so with ``pytestmark = pytest.mark.plugin``.

``UNMARKED_PLUGIN_IMPORTERS`` is a ratchet: the modules that imported the plugin at module level without the mark when
the core-only job arrived (K1: 51 modules). Step P3's K2 PRs empty it -- moving the import into the tests that need it
and marking those, or marking a pure-plugin module whole. It may only shrink: a new module that imports the plugin at
module level must be marked, and an entry that no longer applies must be removed.

This guard runs in the normal suite (it needs the plugin present to mean anything).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import PLUGIN_PACKAGE, _test_modules, module_imports_plugin, plugin_imports_at_module_level

TESTS = Path(__file__).parent

UNMARKED_PLUGIN_IMPORTERS: frozenset[str] = frozenset({
    "test_C3_sms_client.py",
    "test_analyses_facets.py",
    "test_analyses_goldens.py",
    "test_backend_base_url.py",
    "test_backend_config.py",
    "test_backend_dispatch_compose.py",
    "test_backend_interop.py",
    "test_backends_existing.py",
    "test_backends_records.py",
    "test_backends_routing.py",
    "test_backends_select.py",
    "test_backends_startup.py",
    "test_composite_card_dispatch_table.py",
    "test_env_worker_launcher.py",
    "test_job_class_task_routing.py",
    "test_listing_facet.py",
    "test_non_root_store_roots.py",
    "test_opaque_run_ids.py",
    "test_payload_models.py",
    "test_proxy_worker_launcher.py",
    "test_relay_client_timeouts.py",
    "test_remote_analysis_figures_datasets.py",
    "test_remote_analysis_figures_hidden.py",
    "test_remote_build_source.py",
    "test_remote_health.py",
    "test_remote_link.py",
    "test_remote_listing_goldens.py",
    "test_remote_reconcile.py",
    "test_remote_run_jobs.py",
    "test_remote_run_land_artifacts.py",
    "test_remote_run_landing.py",
    "test_remote_run_pinned.py",
    "test_remote_run_poll_hardening.py",
    "test_remote_run_views_lib.py",
    "test_remote_simulations.py",
    "test_remote_study_slug_1to1.py",
    "test_remote_trace.py",
    "test_resolve_cloud_target.py",
    "test_run_artifact_zarr_fallback.py",
    "test_simulations_swr.py",
    "test_sms_api_client.py",
    "test_sms_api_environments.py",
    "test_sms_api_identity.py",
    "test_sms_api_paths.py",
    "test_sms_capabilities.py",
    "test_source_branch.py",
    "test_source_build_views_lib.py",
    "test_store_fetch.py",
    "test_store_fetch_goldens.py",
    "test_trace_dispatch_table.py",
    "test_viva_v1_client_contract.py",
})


def _name(path: Path) -> str:
    return path.relative_to(TESTS).as_posix()


def _declares_plugin_mark(source: str) -> bool:
    """A top-level ``pytestmark = ...`` whose value names the ``plugin`` mark (alone or in a list)."""
    for stmt in ast.parse(source).body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(
            stmt, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets) and stmt.value is not None:
            for node in ast.walk(stmt.value):
                if isinstance(node, ast.Attribute) and node.attr == "plugin" and isinstance(
                        node.value, ast.Attribute) and node.value.attr == "mark":
                    return True
    return False


def _scan() -> tuple[set[str], set[str]]:
    """(module-level importers without the whole-module mark, modules marked whole that never name the plugin)."""
    unmarked, hollow = set(), set()
    for path in _test_modules():
        source = path.read_text(encoding="utf-8")
        marked = _declares_plugin_mark(source)
        if module_imports_plugin(path) and not marked:
            unmarked.add(_name(path))
        if marked and PLUGIN_PACKAGE not in source:
            hollow.add(_name(path))
    return unmarked, hollow


def test_module_level_plugin_importers_are_marked():
    unmarked, _ = _scan()
    new = sorted(unmarked - UNMARKED_PLUGIN_IMPORTERS)
    stale = sorted(UNMARKED_PLUGIN_IMPORTERS - unmarked)
    assert not new, (
        "these test modules import vivarium_workbench_sms at module level without `pytestmark = pytest.mark.plugin`; "
        "the core-only job cannot collect them, so their core tests would silently vanish. Move the import into the "
        f"tests that need it and mark those `@pytest.mark.plugin`, or mark a pure-plugin module whole: {new}")
    assert not stale, (
        f"these no longer import the plugin at module level unmarked; remove them from UNMARKED_PLUGIN_IMPORTERS: "
        f"{stale}")


def test_a_whole_module_plugin_mark_names_the_plugin():
    """A whole-module mark must not hide core tests: the module really references the plugin."""
    _, hollow = _scan()
    assert not hollow, f"marked `plugin` as a whole but never reference {PLUGIN_PACKAGE}: {sorted(hollow)}"


def test_the_plugin_marker_is_registered(pytestconfig):
    assert any(m.startswith("plugin:") for m in pytestconfig.getini("markers"))


@pytest.mark.parametrize(("source", "expected"), [
    ("import vivarium_workbench_sms\n", [1]),
    ("import os\nfrom vivarium_workbench_sms import provider\n", [2]),
    ("from vivarium_workbench_sms.sms_legacy import x as y\n", [1]),
    ("import vivarium_workbench_sms.sms_api_client as c\n", [1]),
    ("import importlib\nm = importlib.import_module('vivarium_workbench_sms.provider')\n", [2]),
    # not the plugin
    ("import vivarium_workbench\nfrom vivarium_workbench.lib import backends\n", []),
    ("import vivarium_workbench_smsx\n", []),
    ("from . import vivarium_workbench_sms\n", []),
    # inside a function: runs at test time, not collection
    ("def test_x():\n    from vivarium_workbench_sms import provider\n", []),
    ("async def f():\n    import vivarium_workbench_sms\n", []),
    # TYPE_CHECKING is not runtime; its else branch is
    ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import vivarium_workbench_sms\n", []),
    ("import typing\nif typing.TYPE_CHECKING:\n    import vivarium_workbench_sms\n", []),
    ("if TYPE_CHECKING:\n    pass\nelse:\n    import vivarium_workbench_sms\n", [4]),
    # other top-level compound statements run at import
    ("import sys\nif sys.version_info > (3,):\n    import vivarium_workbench_sms\n", [3]),
    ("with open('x'):\n    import vivarium_workbench_sms\n", [2]),
    ("class T:\n    from vivarium_workbench_sms import provider\n", [2]),
    # a try that survives a missing plugin does not break collection
    ("try:\n    import vivarium_workbench_sms\nexcept ImportError:\n    pass\n", []),
    ("try:\n    import vivarium_workbench_sms\nexcept (ModuleNotFoundError, KeyError):\n    pass\n", []),
    ("try:\n    import vivarium_workbench_sms\nexcept KeyError:\n    pass\n", [2]),
    ("try:\n    pass\nfinally:\n    import vivarium_workbench_sms\n", [4]),
])
def test_the_detector(source, expected):
    assert plugin_imports_at_module_level(source) == expected


def test_the_mark_check():
    assert _declares_plugin_mark("import pytest\npytestmark = pytest.mark.plugin\n")
    assert _declares_plugin_mark("import pytest\npytestmark = [pytest.mark.slow, pytest.mark.plugin]\n")
    assert not _declares_plugin_mark("import pytest\npytestmark = pytest.mark.slow\n")
    assert not _declares_plugin_mark("import pytest\ndef f():\n    pytestmark = pytest.mark.plugin\n")

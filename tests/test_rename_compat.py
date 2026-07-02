"""Back-compat guarantees for the ``vivarium_dashboard`` -> ``vivarium_workbench`` rename.

Phase 1 keeps every existing external consumer working through deprecated
aliases. These tests lock the three back-compat surfaces so a later refactor
can't silently break them before Phase 3 (when the aliases are removed):

  * the new ``vivarium_workbench`` package imports;
  * the ``vivarium_dashboard`` shim package forwards submodule imports (by
    object identity) to ``vivarium_workbench`` and emits a DeprecationWarning;
  * the six ``vivarium_dashboard.server`` symbols still consumed by external
    repos (v2ecoli / sms-ecoli / pbg-superpowers) resolve; and
  * the dual-read env helper prefers the new prefix but falls back to the old.
"""
from __future__ import annotations

import importlib
import warnings

import pytest


def test_new_package_imports():
    wb = importlib.import_module("vivarium_workbench")
    assert wb.__file__.split("/")[-2] == "vivarium_workbench"


def test_shim_import_emits_deprecation_warning():
    # A fresh import may be cached from another test; re-importing still touches
    # the shim's module object, but the warning only fires on first import per
    # process. Assert the shim module exists and points at the shim package.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # Force a re-exec of the shim __init__ to observe the warning deterministically.
        import vivarium_dashboard  # noqa: F401
        importlib.reload(vivarium_dashboard)
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), \
        "importing vivarium_dashboard should emit a DeprecationWarning"


@pytest.mark.parametrize("sub", [
    "lib.simulations_index",
    "lib.json_serialize",
    "lib.observables_views",
    "api.app",
])
def test_shim_submodule_forwards_by_identity(sub):
    old = importlib.import_module(f"vivarium_dashboard.{sub}")
    new = importlib.import_module(f"vivarium_workbench.{sub}")
    assert old is new, f"vivarium_dashboard.{sub} should be the same module object as the new package"


def test_shim_server_external_symbols_resolve():
    """The six symbols external repos still import from the retired server module."""
    from vivarium_dashboard.server import (  # noqa: F401
        _json_default,
        _json_sanitize,
        _json_body,
        _build_iset_summary_for_test,
        _build_iset_detail_for_test,
        _observables_for_ref,
    )


def test_env_dual_read_prefers_new_falls_back_to_old():
    from vivarium_workbench.lib.env_compat import get_env

    # new prefix wins
    assert get_env("WORKSPACE", env={"VIVARIUM_WORKBENCH_WORKSPACE": "/new"}) == "/new"
    # new wins even when both present
    assert get_env("WORKSPACE", env={
        "VIVARIUM_WORKBENCH_WORKSPACE": "/new",
        "VIVARIUM_DASHBOARD_WORKSPACE": "/old",
    }) == "/new"
    # default when neither present
    assert get_env("WORKSPACE", "fallback", env={}) == "fallback"


def test_env_dual_read_old_prefix_warns():
    from vivarium_workbench.lib import env_compat

    # Reset the once-per-process warning cache so the fallback warning fires here.
    env_compat._warned.discard("VIVARIUM_DASHBOARD_READONLY")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        val = env_compat.get_env("READONLY", env={"VIVARIUM_DASHBOARD_READONLY": "1"})
    assert val == "1"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), \
        "reading the old-prefix env var should emit a DeprecationWarning"

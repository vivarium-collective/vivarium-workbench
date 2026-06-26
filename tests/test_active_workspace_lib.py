"""Tests for vivarium_dashboard.lib.active_workspace — the single source of
truth for the active workspace root + the cache-invalidation callback registry.
"""

import pytest

from vivarium_dashboard.lib import active_workspace, _root


@pytest.fixture(autouse=True)
def _restore_state():
    """Snapshot/restore the registry + root so tests don't leak global state."""
    saved_cbs = list(active_workspace._CLEAR_CBS)
    saved_root = _root.get_workspace_root()
    yield
    active_workspace._CLEAR_CBS[:] = saved_cbs
    _root._WS_ROOT = saved_root


def test_register_clear_cb_is_idempotent():
    active_workspace._CLEAR_CBS[:] = []

    def cb() -> None:
        pass

    active_workspace.register_clear_cb(cb)
    active_workspace.register_clear_cb(cb)  # second registration is a no-op
    assert active_workspace._registered_cbs().count(cb) == 1
    assert len(active_workspace._registered_cbs()) == 1


def test_invalidate_calls_every_registered_cb():
    active_workspace._CLEAR_CBS[:] = []
    fired: list[str] = []
    active_workspace.register_clear_cb(lambda: fired.append("a"))
    active_workspace.register_clear_cb(lambda: fired.append("b"))

    active_workspace.invalidate()

    assert sorted(fired) == ["a", "b"]


def test_get_set_workspace_root_delegates_to_root(tmp_path):
    active_workspace.set_workspace_root(tmp_path)
    # Round-trips, and BOTH the facade and _root see the same single value.
    assert active_workspace.get_workspace_root() == tmp_path.resolve()
    assert _root.get_workspace_root() == tmp_path.resolve()
    assert active_workspace.get_workspace_root() is _root._WS_ROOT


def test_lib_caches_registered_on_import():
    """Importing the cache modules registers ≥5 lib clear_cache callbacks."""
    # Import side effects register the callbacks at module import time.
    from vivarium_dashboard.lib import (  # noqa: F401
        registry,
        report_views,
        observables_views,
        composite_state_views,
        data_sources,
    )

    cbs = active_workspace._registered_cbs()
    for mod in (registry, report_views, observables_views,
                composite_state_views, data_sources):
        assert mod.clear_cache in cbs
    assert len(cbs) >= 5

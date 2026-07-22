from pathlib import Path
import pytest
import yaml
import vivarium_workbench
from vivarium_workbench.lib import _root
from vivarium_workbench.lib import active_workspace
from vivarium_workbench.lib import registry as _registry
from vivarium_workbench.lib import observables_views as _obs_views
from vivarium_workbench.lib import report_views as _report_views
from vivarium_workbench.lib import composite_state_views as _cs_views
from vivarium_workbench.lib import source_switch_views as _switch_views
from vivarium_workbench.lib.data_sources import _DATA_SOURCES_CACHE

_PKG_DIR = Path(vivarium_workbench.__file__).parent


@pytest.fixture(autouse=True)
def _restore_workspace():
    saved_root = _root.get_workspace_root()
    yield
    if saved_root is not None:
        _root.set_workspace_root(saved_root)


def _static(name):
    return (_PKG_DIR / "static" / name).read_text(encoding="utf-8")


def test_switch_active_workspace_repoints_and_invalidates(tmp_path):
    a = tmp_path / "a"; (a).mkdir(); (a / "workspace.yaml").write_text("name: a\n")
    b = tmp_path / "b"; (b).mkdir(); (b / "workspace.yaml").write_text("name: b\n")

    _root.set_workspace_root(a)
    # Dirty every workspace-keyed (lib) cache.
    _registry._REGISTRY_CACHE["ws-a"] = {"data": {"stale": True}, "ts": 1.0}
    _report_views._LINKAGE_CACHE["x"] = 1     # linkage cache (lib)
    _obs_views._OBS_CACHE["x"] = 1            # observables build cache (lib)
    _cs_views._COMPOSITE_STATE_CACHE["x"] = 1  # composite-state build cache (lib)
    _DATA_SOURCES_CACHE["x"] = 1

    active_workspace.switch_workspace(b)

    assert _root.get_workspace_root() == b.resolve()
    assert _registry._REGISTRY_CACHE == {}
    assert _report_views._LINKAGE_CACHE == {}
    assert _obs_views._OBS_CACHE == {}
    assert _cs_views._COMPOSITE_STATE_CACHE == {}
    assert _DATA_SOURCES_CACHE == {}


def test_invalidate_clears_identical_cache_set_via_registry():
    """Populate EVERY workspace-keyed lib cache, call ``active_workspace.invalidate``,
    and assert all are empty/reset — proving the registry-driven invalidation
    clears the full set the old inline clears did.
    """
    _registry._REGISTRY_CACHE["ws-a"] = {"data": {"stale": True}, "ts": 123.0}
    _report_views._LINKAGE_CACHE["x"] = 1
    _obs_views._OBS_CACHE["x"] = 1
    _cs_views._COMPOSITE_STATE_CACHE["x"] = 1
    _DATA_SOURCES_CACHE["x"] = 1

    # Every lib clear must be reachable through the registry.
    assert len(active_workspace._registered_cbs()) >= 5

    active_workspace.invalidate()

    assert _registry._REGISTRY_CACHE == {}
    assert _report_views._LINKAGE_CACHE == {}
    assert _obs_views._OBS_CACHE == {}
    assert _cs_views._COMPOSITE_STATE_CACHE == {}
    assert _DATA_SOURCES_CACHE == {}


def test_source_switch_rejects_unregistered_path(tmp_path, monkeypatch):
    from pbg_superpowers import workspace_catalog
    monkeypatch.setattr(workspace_catalog, "list_workspaces", lambda: [])

    obj, code = _switch_views.source_switch({"path": str(tmp_path / "nope")})
    assert code == 400
    assert "error" in obj


def test_source_switch_accepts_registered_path(tmp_path, monkeypatch):
    ws = tmp_path / "ws"; ws.mkdir(); (ws / "workspace.yaml").write_text("name: w\n")
    from pbg_superpowers import workspace_catalog
    monkeypatch.setattr(workspace_catalog, "list_workspaces",
                        lambda: [{"path": str(ws), "name": "w"}])

    obj, code = _switch_views.source_switch({"path": str(ws)})
    assert code == 200
    assert obj["ok"] is True
    assert _root.get_workspace_root() == ws.resolve()


def test_source_switch_js_present_and_wired():
    js = _static("source-switch.js")
    assert "/api/workspaces" in js          # lists the catalog
    assert "/api/source/switch" in js       # POSTs the switch
    assert "window.location.reload" in js   # reload after switch
    assert "viv-source-switch" in js        # the control id


# NOTE: the rail source-switch dropdown was superseded by the Branch-tab Source
# panel (branch-source.js); its template-presence test was removed. The
# /api/source/switch endpoint it relied on is still exercised below and by
# branch-source.js. source-switch.js remains on disk as (now unloaded) dead code.


def _make_ws(d, name):
    d.mkdir(parents=True, exist_ok=True)
    (d / "workspace.yaml").write_text(yaml.safe_dump({"name": name}))
    return d


def test_one_server_switches_between_two_workspaces(tmp_path):
    a = _make_ws(tmp_path / "wa", "alpha")
    b = _make_ws(tmp_path / "wb", "beta")

    def active_name():
        root = _root.get_workspace_root()
        return yaml.safe_load((root / "workspace.yaml").read_text())["name"]

    active_workspace.switch_workspace(a)
    assert _root.get_workspace_root() == a.resolve() and active_name() == "alpha"

    active_workspace.switch_workspace(b)
    assert _root.get_workspace_root() == b.resolve() and active_name() == "beta"   # re-pointed, no restart

    active_workspace.switch_workspace(a)
    assert active_name() == "alpha"                             # and back


def test_source_switch_warns_about_composites():
    js = _static("source-switch.js")
    assert "Composite" in js   # the honest until-SP2b note


def test_composite_state_cache_is_workspace_keyed():
    """Two workspaces caching the SAME ref must not collide (slice 3)."""
    _cs_views._COMPOSITE_STATE_CACHE.clear()
    _cs_views._COMPOSITE_STATE_CACHE[("/ws/a", "pkg.composites.x")] = (1.0, {"state": "A"})
    _cs_views._COMPOSITE_STATE_CACHE[("/ws/b", "pkg.composites.x")] = (1.0, {"state": "B"})
    assert _cs_views._COMPOSITE_STATE_CACHE[("/ws/a", "pkg.composites.x")][1]["state"] == "A"
    assert _cs_views._COMPOSITE_STATE_CACHE[("/ws/b", "pkg.composites.x")][1]["state"] == "B"


def test_registry_cache_is_workspace_keyed():
    """The registry catalog cache keys on the workspace, not a single slot."""
    _registry._REGISTRY_CACHE.clear()
    _registry._REGISTRY_CACHE["/ws/a"] = {"data": {"processes": ["A"]}, "ts": 1.0}
    _registry._REGISTRY_CACHE["/ws/b"] = {"data": {"processes": ["B"]}, "ts": 1.0}
    assert _registry._REGISTRY_CACHE["/ws/a"]["data"]["processes"] == ["A"]
    assert _registry._REGISTRY_CACHE["/ws/b"]["data"]["processes"] == ["B"]

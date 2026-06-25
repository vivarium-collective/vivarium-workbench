from pathlib import Path
import pytest
import yaml
from vivarium_dashboard import server
from vivarium_dashboard.lib import _root
from vivarium_dashboard.lib import observables_views as _obs_views
from vivarium_dashboard.lib import report_views as _report_views
from vivarium_dashboard.lib.data_sources import _DATA_SOURCES_CACHE


@pytest.fixture(autouse=True)
def _restore_workspace():
    saved_ws = getattr(server, "WORKSPACE", None)
    saved_root = _root.get_workspace_root()
    yield
    server.WORKSPACE = saved_ws
    if saved_root is not None:
        _root.set_workspace_root(saved_root)


def _static(name):
    return (Path(server.__file__).parent / "static" / name).read_text(encoding="utf-8")


def test_switch_active_workspace_repoints_and_invalidates(tmp_path):
    a = tmp_path / "a"; (a).mkdir(); (a / "workspace.yaml").write_text("name: a\n")
    b = tmp_path / "b"; (b).mkdir(); (b / "workspace.yaml").write_text("name: b\n")

    server.WORKSPACE = a
    _root.set_workspace_root(a)
    # Dirty every workspace-keyed cache.
    server._REGISTRY_CACHE["data"] = {"stale": True}
    _report_views._LINKAGE_CACHE["x"] = 1     # linkage cache moved to lib
    _obs_views._OBS_CACHE["x"] = 1            # observables build cache (lib)
    server._COMPOSITE_STATE_CACHE["x"] = 1
    server._RUN_STORE_SUMMARY_CACHE["x"] = 1
    server._WP_CACHE["x"] = 1
    _DATA_SOURCES_CACHE["x"] = 1

    server._switch_active_workspace(b)

    assert server.WORKSPACE == b.resolve()
    assert _root.get_workspace_root() == b.resolve()
    assert server._REGISTRY_CACHE["data"] is None
    assert server._REGISTRY_CACHE["ts"] == 0.0
    assert _report_views._LINKAGE_CACHE == {}
    assert _obs_views._OBS_CACHE == {}
    assert server._COMPOSITE_STATE_CACHE == {}
    assert server._RUN_STORE_SUMMARY_CACHE == {}
    assert server._WP_CACHE == {}
    assert _DATA_SOURCES_CACHE == {}


def test_source_switch_route_registered():
    assert server._POST_ROUTE_MAP.get("/api/source/switch") == "_post_source_switch"


def test_source_switch_rejects_unregistered_path(tmp_path, monkeypatch):
    from pbg_superpowers import workspace_catalog
    monkeypatch.setattr(workspace_catalog, "list_workspaces", lambda: [])
    captured = {}

    class FakeHandler:
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._post_source_switch(FakeHandler(), {"path": str(tmp_path / "nope")})
    assert captured["code"] == 400
    assert "error" in captured["obj"]


def test_source_switch_accepts_registered_path(tmp_path, monkeypatch):
    ws = tmp_path / "ws"; ws.mkdir(); (ws / "workspace.yaml").write_text("name: w\n")
    from pbg_superpowers import workspace_catalog
    monkeypatch.setattr(workspace_catalog, "list_workspaces",
                        lambda: [{"path": str(ws), "name": "w"}])
    captured = {}

    class FakeHandler:
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._post_source_switch(FakeHandler(), {"path": str(ws)})
    assert captured["code"] == 200
    assert captured["obj"]["ok"] is True
    assert server.WORKSPACE == ws.resolve()


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

    server._switch_active_workspace(a)
    assert server.WORKSPACE == a and active_name() == "alpha"

    server._switch_active_workspace(b)
    assert server.WORKSPACE == b and active_name() == "beta"   # re-pointed, no restart

    server._switch_active_workspace(a)
    assert active_name() == "alpha"                             # and back


def test_source_switch_warns_about_composites():
    js = _static("source-switch.js")
    assert "Composite" in js   # the honest until-SP2b note

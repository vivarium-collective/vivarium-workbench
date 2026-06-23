from pathlib import Path
from vivarium_dashboard import server
from vivarium_dashboard.lib import _root
from vivarium_dashboard.lib.data_sources import _DATA_SOURCES_CACHE


def test_switch_active_workspace_repoints_and_invalidates(tmp_path):
    a = tmp_path / "a"; (a).mkdir(); (a / "workspace.yaml").write_text("name: a\n")
    b = tmp_path / "b"; (b).mkdir(); (b / "workspace.yaml").write_text("name: b\n")

    server.WORKSPACE = a
    _root.set_workspace_root(a)
    # Dirty every workspace-keyed cache.
    server._REGISTRY_CACHE["data"] = {"stale": True}
    server._LINKAGE_CACHE["x"] = 1
    server._COMPOSITE_STATE_CACHE["x"] = 1
    server._RUN_STORE_SUMMARY_CACHE["x"] = 1
    server._WP_CACHE["x"] = 1
    _DATA_SOURCES_CACHE["x"] = 1

    server._switch_active_workspace(b)

    assert server.WORKSPACE == b.resolve()
    assert _root.get_workspace_root() == b.resolve()
    assert server._REGISTRY_CACHE["data"] is None
    assert server._REGISTRY_CACHE["ts"] == 0.0
    assert server._LINKAGE_CACHE == {}
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

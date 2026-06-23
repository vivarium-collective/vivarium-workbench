from pathlib import Path
from vivarium_dashboard import server
from vivarium_dashboard.lib import _root


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

    server._switch_active_workspace(b)

    assert server.WORKSPACE == b
    assert _root.get_workspace_root() == b
    assert server._REGISTRY_CACHE["data"] is None
    assert server._LINKAGE_CACHE == {}
    assert server._COMPOSITE_STATE_CACHE == {}
    assert server._RUN_STORE_SUMMARY_CACHE == {}
    assert server._WP_CACHE == {}

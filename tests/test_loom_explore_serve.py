"""Test that the loom-explore static bundle is served by the dashboard.

After the extraction from pbg-template, the bundle lives inside the
``vivarium_workbench`` package (``vivarium_workbench/static/loom-explore/``)
rather than the workspace's ``scripts/_assets/`` tree. These tests verify
that the server still serves the bundle correctly from the new location.
"""
import json
import urllib.request
import urllib.error
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def workspace_server(tmp_path, dashboard_client):
    ws_root = tmp_path
    (ws_root / "workspace.yaml").write_text(yaml.dump({
        "name": "testws",
        "package_path": "pbg_testws",
    }, sort_keys=False))

    client = dashboard_client(ws_root)

    class _WS:
        url = client.base_url
        root = ws_root

    yield _WS()


def test_loom_explore_index_served(workspace_server):
    with urllib.request.urlopen(workspace_server.url + "/loom-explore/index.html") as resp:
        assert resp.status == 200
        body = resp.read().decode()
        # The bundled prod build is a Vite-generated SPA; index.html should
        # contain a <script> tag for the bundled JS or a doctype.
        assert "<html" in body.lower() or "<!doctype" in body.lower()


def test_loom_explore_root_redirects_to_index(workspace_server):
    """Visiting /loom-explore/ (trailing slash) should serve index.html."""
    try:
        with urllib.request.urlopen(workspace_server.url + "/loom-explore/") as resp:
            assert resp.status == 200
            body = resp.read().decode()
            assert "<html" in body.lower() or "<!doctype" in body.lower()
    except urllib.error.HTTPError as e:
        # Acceptable if the server returns 200 directly; flag if 404
        assert e.code == 200, f"expected 200, got {e.code}"


def test_loom_explore_js_assets_served(workspace_server):
    """Every .js file under assets/ should be servable."""
    import vivarium_workbench
    bundle = Path(vivarium_workbench.__file__).parent / "static" / "loom-explore" / "assets"
    js_files = list(bundle.glob("*.js"))
    if not js_files:
        pytest.skip("no JS files in bundled loom-explore/assets")
    for js in js_files:
        url = workspace_server.url + "/loom-explore/assets/" + js.name
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200, f"{js.name}: HTTP {resp.status}"


def test_loom_explore_css_assets_served(workspace_server):
    """Every .css file under assets/ should be servable."""
    import vivarium_workbench
    bundle = Path(vivarium_workbench.__file__).parent / "static" / "loom-explore" / "assets"
    css_files = list(bundle.glob("*.css"))
    if not css_files:
        pytest.skip("no CSS files in bundled loom-explore/assets")
    for css in css_files:
        url = workspace_server.url + "/loom-explore/assets/" + css.name
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200
            assert "text/css" in (resp.headers.get("Content-Type", "") or "")


def test_loom_explore_path_traversal_refused(workspace_server):
    """A path with .. must be refused."""
    try:
        urllib.request.urlopen(workspace_server.url + "/loom-explore/../workspace.yaml")
        raise AssertionError("expected refusal")
    except urllib.error.HTTPError as e:
        assert e.code in (403, 404), f"expected 403/404, got {e.code}"


def test_loom_explore_missing_file_404(workspace_server):
    try:
        urllib.request.urlopen(workspace_server.url + "/loom-explore/assets/nonexistent.js")
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_ui_config_default_is_loom_explore(workspace_server):
    """When no ui block is set in workspace.yaml, the default is loom-explore."""
    with urllib.request.urlopen(workspace_server.url + "/api/ui-config") as resp:
        data = json.loads(resp.read())
    assert data["composite_view"] == "loom-explore"


def test_ui_config_respects_workspace_flag(workspace_server):
    """Setting ui.composite_view in workspace.yaml flips the flag."""
    ws_file = workspace_server.root / "workspace.yaml"
    ws = yaml.safe_load(ws_file.read_text()) or {}
    ws["ui"] = {"composite_view": "bigraph-viz"}
    ws_file.write_text(yaml.safe_dump(ws, sort_keys=False))
    with urllib.request.urlopen(workspace_server.url + "/api/ui-config") as resp:
        data = json.loads(resp.read())
    assert data["composite_view"] == "bigraph-viz"

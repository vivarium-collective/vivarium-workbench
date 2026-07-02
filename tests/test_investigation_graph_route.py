import yaml
import pytest
from fastapi.testclient import TestClient
from vivarium_workbench.api.app import create_app, get_workspace
from vivarium_workbench.lib import active_workspace


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root()
    active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    inv = tmp_path / "investigations" / "demo-inv"; inv.mkdir(parents=True)
    inv.joinpath("investigation.yaml").write_text(yaml.safe_dump(
        {"name": "demo-inv", "studies": ["s1"]}))
    s1 = tmp_path / "studies" / "s1"; s1.mkdir(parents=True)
    s1.joinpath("study.yaml").write_text(yaml.safe_dump(
        {"schema_version": 4, "name": "s1", "title": "First", "status": "complete"}))
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def test_graph_route_200(client):
    r = client.get("/api/investigation-graph?investigation=demo-inv")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["investigation"] == "demo-inv"
    assert {s["id"] for s in body["studies"]} == {"study/s1"}
    assert "chains" in body and "study_edges" in body


def test_graph_route_unknown_404(client):
    r = client.get("/api/investigation-graph?investigation=nope")
    assert r.status_code == 404

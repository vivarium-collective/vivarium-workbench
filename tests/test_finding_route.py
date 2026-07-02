import pytest
from fastapi.testclient import TestClient
from vivarium_workbench.api.app import create_app, get_workspace
from vivarium_workbench.lib import active_workspace
from investigation_contracts import read_log
from vivarium_workbench.lib.event_log import log_path


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root()
    active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "studies" / "demo").mkdir(parents=True)
    (tmp_path / "studies" / "demo" / "study.yaml").write_text("name: demo\n")
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def test_post_finding_writes_node_and_emits_after(client, ws):
    r = client.post("/api/finding", json={"study": "demo", "statement": "X up with Y", "runs": ["run/1"]})
    assert r.status_code == 200, r.text
    body = r.json()
    fid = body["finding_id"]
    # finding node file exists
    assert (ws / "studies" / "demo" / "findings" / f"{fid}.yaml").is_file()
    # event emitted, references the finding (emit-after-commit: file exists too)
    events = read_log(log_path(ws), types=["FindingCreated"])
    assert len(events) == 1 and events[0]["payload"]["study"] == "demo"
    assert body["event_id"] == events[0]["event_id"]


def test_post_finding_missing_study_404(client):
    r = client.post("/api/finding", json={"study": "nope", "statement": "s", "runs": []})
    assert r.status_code == 404

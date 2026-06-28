import pytest
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace
from vivarium_dashboard.lib.event_log import log_path
from investigation_contracts import read_log


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root(); active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "studies" / "demo").mkdir(parents=True)
    (tmp_path / "studies" / "demo" / "study.yaml").write_text("name: demo\n")
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def test_post_evidence_writes_and_emits(client, ws):
    r = client.post("/api/evidence", json={"study": "demo", "findings": ["finding/f1"],
                                           "hypotheses": ["H rises"], "confidence": 0.8, "statement": "s"})
    assert r.status_code == 200, r.text
    eid = r.json()["evidence_id"]
    assert (ws / "studies" / "demo" / "evidence" / f"{eid}.yaml").is_file()
    evs = read_log(log_path(ws), types=["EvidenceLinked"])
    assert len(evs) == 1 and r.json()["event_id"] == evs[0]["event_id"]


def test_post_evidence_requires_finding_and_hypothesis(client):
    r = client.post("/api/evidence", json={"study": "demo", "findings": [], "hypotheses": ["H"]})
    assert r.status_code == 400
    r = client.post("/api/evidence", json={"study": "demo", "findings": ["finding/f1"], "hypotheses": []})
    assert r.status_code == 400


def test_post_evidence_missing_study_404(client):
    r = client.post("/api/evidence", json={"study": "nope", "findings": ["finding/f1"], "hypotheses": ["H"]})
    assert r.status_code == 404

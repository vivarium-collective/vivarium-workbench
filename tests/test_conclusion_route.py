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
    d = tmp_path / "studies" / "demo"; d.mkdir(parents=True)
    (d / "study.yaml").write_text("name: demo\n")
    (d / "findings").mkdir()
    import yaml
    (d / "findings" / "f1.yaml").write_text(yaml.safe_dump(
        {"id": "finding/f1", "type": "finding", "lifecycle_state": "proposed", "runs": ["run/1"]}))
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def _evidence(client):
    return client.post("/api/evidence", json={"study": "demo", "findings": ["finding/f1"],
                                              "hypotheses": ["H"], "confidence": 0.5}).json()["evidence_id"]


def test_conclusion_before_decision_is_422(client, ws):
    eid = _evidence(client)
    r = client.post("/api/conclusion", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                             "decisions": [], "statement": "C"})
    assert r.status_code == 422
    assert r.json()["violations"]
    # nothing written / emitted
    assert not (ws / "studies" / "demo" / "conclusions").exists()
    assert read_log(log_path(ws), types=["ConclusionPublished"]) == []


def test_conclusion_after_accept_decision_publishes(client, ws):
    eid = _evidence(client)
    did = client.post("/api/decision", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                             "outcome": "accept"}).json()["decision_id"]
    r = client.post("/api/conclusion", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                             "decisions": [f"decision/{did}"], "statement": "C"})
    assert r.status_code == 200, r.text
    cid = r.json()["conclusion_id"]
    import yaml
    node = yaml.safe_load((ws / "studies" / "demo" / "conclusions" / f"{cid}.yaml").read_text())
    assert node["lifecycle_state"] == "published"
    assert read_log(log_path(ws), types=["ConclusionPublished"])[-1]["event_id"] == r.json()["event_id"]

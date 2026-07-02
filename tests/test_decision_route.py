import pytest, yaml
from fastapi.testclient import TestClient
from vivarium_workbench.api.app import create_app, get_workspace
from vivarium_workbench.lib import active_workspace


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root(); active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "studies" / "demo"; (d).mkdir(parents=True)
    (d / "study.yaml").write_text("name: demo\n")
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def _evidence_id(client):
    r = client.post("/api/evidence", json={"study": "demo", "findings": ["finding/f1"],
                                           "hypotheses": ["H"], "confidence": 0.5})
    return r.json()["evidence_id"]


def test_accept_decision_advances_evidence(client, ws):
    eid = _evidence_id(client)
    r = client.post("/api/decision", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                           "outcome": "accept", "rationale": "ok", "decided_by": "curator"})
    assert r.status_code == 200, r.text
    did = r.json()["decision_id"]
    assert (ws / "studies" / "demo" / "decisions" / f"{did}.yaml").is_file()
    ev = yaml.safe_load((ws / "studies" / "demo" / "evidence" / f"{eid}.yaml").read_text())
    assert ev["lifecycle_state"] == "accepted"


def test_reject_decision_rejects_evidence(client, ws):
    eid = _evidence_id(client)
    client.post("/api/decision", json={"study": "demo", "evidence": [f"evidence/{eid}"], "outcome": "reject"})
    ev = yaml.safe_load((ws / "studies" / "demo" / "evidence" / f"{eid}.yaml").read_text())
    assert ev["lifecycle_state"] == "rejected"


def test_bad_outcome_422_from_pydantic(client):
    r = client.post("/api/decision", json={"study": "demo", "evidence": [], "outcome": "maybe"})
    assert r.status_code == 422  # pydantic Literal rejection

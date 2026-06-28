import pytest, yaml
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


def test_finding_to_conclusion_closed_chain(tmp_path):
    d = tmp_path / "studies" / "demo"; d.mkdir(parents=True)
    (d / "study.yaml").write_text("name: demo\n")
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: tmp_path
    c = TestClient(app)

    fid = c.post("/api/finding", json={"study": "demo", "statement": "X up with Y", "runs": ["run/1"]}).json()["finding_id"]
    eid = c.post("/api/evidence", json={"study": "demo", "findings": [f"finding/{fid}"],
                                        "hypotheses": ["Y drives X"], "confidence": 0.9}).json()["evidence_id"]
    # conclusion before a decision is refused
    assert c.post("/api/conclusion", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                           "decisions": [], "statement": "C"}).status_code == 422
    did = c.post("/api/decision", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                        "outcome": "accept", "decided_by": "curator"}).json()["decision_id"]
    r = c.post("/api/conclusion", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                        "decisions": [f"decision/{did}"], "statement": "C"})
    assert r.status_code == 200, r.text
    # all four node files exist; ConclusionPublished is the last event
    for sub, nid in (("findings", fid), ("evidence", eid), ("decisions", did), ("conclusions", r.json()["conclusion_id"])):
        assert (tmp_path / "studies" / "demo" / sub / f"{nid}.yaml").is_file()
    events = read_log(log_path(tmp_path))
    assert events[-1]["type"] == "ConclusionPublished"
    assert [e["type"] for e in events] == ["FindingCreated", "EvidenceLinked", "DecisionRecorded", "ConclusionPublished"]

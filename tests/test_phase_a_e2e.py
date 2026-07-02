import pytest
from fastapi.testclient import TestClient
from vivarium_workbench.api.app import create_app, get_workspace
from vivarium_workbench.lib import active_workspace
from pbg_superpowers.event_client import EventClient, on_finding_created


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root(); active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


def test_finding_to_reaction_closed_loop(tmp_path):
    (tmp_path / "studies" / "demo").mkdir(parents=True)
    (tmp_path / "studies" / "demo" / "study.yaml").write_text("name: demo\n")
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: tmp_path
    client = TestClient(app)

    # computational spine: write a Finding → emit FindingCreated
    r = client.post("/api/finding", json={"study": "demo", "statement": "X up with Y", "runs": ["run/1"]})
    assert r.status_code == 200, r.text
    fid = r.json()["finding_id"]

    # agentic spine: react
    c = EventClient(tmp_path, consumer="e2e")
    c.on("FindingCreated", lambda ev: on_finding_created(tmp_path, ev))
    assert c.poll_once() == 1

    # the loop closed: a reaction record references the finding
    eid = r.json()["event_id"]
    rec = (tmp_path / ".pbg" / "reactions" / f"{eid}.yaml")
    assert rec.is_file()
    import yaml
    assert yaml.safe_load(rec.read_text())["finding_id"] == fid
    # idempotent: re-poll from scratch cursor handles nothing new
    assert c.poll_once() == 0

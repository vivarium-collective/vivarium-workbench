import pytest
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace, event_log


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root(); active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


def _prov():
    return {"actor": "agentic", "agent_id": "p", "timestamp": "t",
            "source_objects": [], "justification": "j", "tool": "", "commit": ""}


@pytest.fixture
def ws(tmp_path):
    for s in ("f/1", "f/2"):
        event_log.emit_event(tmp_path, type="FindingCreated", subject=s,
                             transition={"from": "", "to": "proposed"}, actor="agentic",
                             provenance=_prov(), payload={"study": "demo"})
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def test_replay_all_then_close(client):
    # ?once=1 returns history and closes (test-only bounded mode)
    r = client.get("/api/events/log?once=1")
    assert r.status_code == 200
    ids = [ln[4:] for ln in r.text.splitlines() if ln.startswith("id: ")]
    assert ids == ["000000000001", "000000000002"]


def test_since_cursor(client):
    r = client.get("/api/events/log?once=1&since=000000000001")
    ids = [ln[4:] for ln in r.text.splitlines() if ln.startswith("id: ")]
    assert ids == ["000000000002"]

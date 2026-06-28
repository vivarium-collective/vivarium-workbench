# tests/test_study_readouts_route.py
import pytest
from fastapi.testclient import TestClient

from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace


@pytest.fixture(autouse=True)
def _reset_active_workspace():
    saved = active_workspace.get_workspace_root()
    active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: tmp_path
    return TestClient(app)


def test_study_readouts_invalid_slug_400(client):
    assert client.get("/api/study-readouts?study=Bad Slug!").status_code == 400


def test_study_readouts_missing_study_404(client):
    assert client.get("/api/study-readouts?study=nope").status_code == 404

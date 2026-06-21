"""Live endpoint: GET /api/investigation-notebook/<slug>[?format=py].

Exercises the interactive-mode download route against a throwaway copy of the
ws_increase_demo fixture (which ships a 'baseline' investigation), asserting the
server generates and returns a valid notebook / script on demand.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


@pytest.fixture
def ws_copy(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURE, ws)
    return ws


def test_investigation_notebook_endpoint_ipynb(dashboard_client, ws_copy):
    client = dashboard_client(workspace=ws_copy)
    resp = client.get("/api/investigation-notebook/baseline")
    assert resp.status_code == 200
    nb = json.loads(resp.text)
    assert nb["nbformat"] == 4 and nb["cells"]


def test_investigation_notebook_endpoint_py(dashboard_client, ws_copy):
    client = dashboard_client(workspace=ws_copy)
    resp = client.get("/api/investigation-notebook/baseline?format=py")
    assert resp.status_code == 200
    # the generated script must be syntactically valid Python
    compile(resp.text, "<endpoint>", "exec")


def test_investigation_notebook_endpoint_404(dashboard_client, ws_copy):
    client = dashboard_client(workspace=ws_copy)
    resp = client.get("/api/investigation-notebook/does-not-exist")
    assert resp.status_code == 404

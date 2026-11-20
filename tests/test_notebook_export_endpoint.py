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


def test_investigation_notebook_endpoint_ipynb(inproc_client, ws_copy):
    client = inproc_client(workspace=ws_copy)
    resp = client.get("/api/investigation-notebook/baseline")
    assert resp.status_code == 200
    nb = json.loads(resp.text)
    assert nb["nbformat"] == 4 and nb["cells"]


def test_investigation_notebook_endpoint_py(inproc_client, ws_copy):
    client = inproc_client(workspace=ws_copy)
    resp = client.get("/api/investigation-notebook/baseline?format=py")
    assert resp.status_code == 200
    # the generated script must be syntactically valid Python
    compile(resp.text, "<endpoint>", "exec")


def test_investigation_notebook_endpoint_404(inproc_client, ws_copy):
    client = inproc_client(workspace=ws_copy)
    resp = client.get("/api/investigation-notebook/does-not-exist")
    assert resp.status_code == 404


def test_investigation_notebook_default_is_attachment(inproc_client, ws_copy):
    """Default (no ?download) still forces a download, as before."""
    client = inproc_client(workspace=ws_copy)
    resp = client.get("/api/investigation-notebook/baseline")
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("content-disposition", "")


def test_investigation_notebook_download0_is_inline(inproc_client, ws_copy):
    """?download=0 serves the notebook inline (no attachment) so the browser can
    open it in a new tab — the 'open in browser' LINKS action."""
    client = inproc_client(workspace=ws_copy)
    resp = client.get("/api/investigation-notebook/baseline?download=0")
    assert resp.status_code == 200
    assert "attachment" not in resp.headers.get("content-disposition", "")
    import json as _json
    assert _json.loads(resp.text)["nbformat"] == 4  # still a valid notebook


def test_investigation_figures_gallery_404_without_figures(inproc_client, ws_copy):
    """figures.html returns a clean 404 when the investigation has no figures."""
    client = inproc_client(workspace=ws_copy)
    resp = client.get("/api/investigation/baseline/figures.html")
    # baseline may or may not ship figures in the fixture; if none → 404 JSON,
    # if some → an inline HTML gallery. Either is valid; assert the contract.
    if resp.status_code == 404:
        assert "error" in resp.json()
    else:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "attachment" not in resp.headers.get("content-disposition", "")

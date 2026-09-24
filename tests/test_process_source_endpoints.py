"""Route-level coverage for the process-source read/write endpoints.

Spins the live FastAPI app against a copy of the increase-demo fixture and
exercises the two endpoints the code rail calls:

  * GET  /api/registry/process-source  → source + editable flag
  * POST /api/registry/process-source  → save back to the workspace file
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
_ADDR = "pbg_ws_increase_demo.processes.IncreaseProcess"


@pytest.fixture()
def ws_copy(tmp_path):
    dst = tmp_path / "ws_increase_demo"
    shutil.copytree(_FIXTURE, dst)
    return dst


def test_get_process_source(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    r = client.get(f"/api/registry/process-source?address={_ADDR}")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "class IncreaseProcess" in body["source"]
    assert body["editable"] is True


def test_post_process_source_saves_file(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    proc_file = ws_copy / "pbg_ws_increase_demo" / "processes.py"
    edited = proc_file.read_text().replace("rate', 1.0", "rate', 2.0")
    r = client.post(
        "/api/registry/process-source",
        json={"address": _ADDR, "source": edited},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert proc_file.read_text() == edited


def test_post_process_source_rejects_bad_syntax(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    proc_file = ws_copy / "pbg_ws_increase_demo" / "processes.py"
    original = proc_file.read_text()
    r = client.post(
        "/api/registry/process-source",
        json={"address": _ADDR, "source": "def broken(:\n"},
    )
    body = r.json()
    assert body["ok"] is False
    assert "syntax" in body["error"].lower()
    assert proc_file.read_text() == original  # untouched

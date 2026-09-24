"""Route-level coverage for the composite-source read/write endpoints."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
_SPEC_PATH = "pbg_ws_increase_demo/composites/increase-demo.composite.yaml"
_SPEC_ID = "pbg_ws_increase_demo.composites.increase-demo"
_GEN_ID = "pbg_ws_increase_demo.composites.hint_test"
_GEN_MODULE = "pbg_ws_increase_demo.composites"


@pytest.fixture()
def ws_copy(tmp_path):
    dst = tmp_path / "ws_increase_demo"
    shutil.copytree(_FIXTURE, dst)
    return dst


def test_get_spec_composite_source(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    r = client.get("/api/composites/source?id=" + _SPEC_ID + "&source_path=" + _SPEC_PATH)
    body = r.json()
    assert body["ok"] is True
    assert body["lang"] == "yaml"
    assert "name: increase-demo" in body["source"]


def test_get_generator_composite_source(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    r = client.get("/api/composites/source?id=" + _GEN_ID + "&module=" + _GEN_MODULE)
    body = r.json()
    assert body["ok"] is True
    assert body["lang"] == "python"
    assert "def hint_test" in body["source"]


def test_post_spec_composite_source_saves(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    yaml_file = ws_copy / _SPEC_PATH
    edited = yaml_file.read_text().replace("default: 2.0", "default: 4.0")
    r = client.post("/api/composites/source", json={
        "id": _SPEC_ID, "source_path": _SPEC_PATH, "lang": "yaml", "source": edited,
    })
    assert r.json()["ok"] is True
    assert yaml_file.read_text() == edited

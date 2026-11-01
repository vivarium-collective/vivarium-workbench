"""Live endpoint: GET /api/analysis-tools.

Exercises the tools-first Analysis Tools tab endpoint against a throwaway
copy of the ws_increase_demo fixture. The fixture has no runs/packs, so
built-in tools appear with an empty `matched` list.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


@pytest.fixture
def ws_copy(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURE, ws)
    return ws


def test_analysis_tools_endpoint(inproc_client, ws_copy):
    client = inproc_client(workspace=ws_copy)
    r = client.get("/api/analysis-tools")
    assert r.status_code == 200
    body = r.json()
    assert "tools" in body
    ids = {t["id"] for t in body["tools"]}
    # Data Explorer was removed from the core built-ins entirely.
    assert "data-explorer" not in ids
    # Built-ins are only surfaced where their capability is present, so every
    # built-in tool that DOES appear must carry a non-empty match.
    for t in body["tools"]:
        assert "requires" in t and "matched" in t
        if t["id"] == "parsimony-viewer":
            assert t["matched"]  # only shown when a 3d_pack exists

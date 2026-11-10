"""Analysis Tools tab: tools-first frontend + removed header/paragraph.

The tab no longer carries a redundant in-page ``<h2>Analysis Tools</h2>``
(the nav rail already labels it) nor the descriptive "Interactive scenes…"
paragraph. Its cards are driven by ``GET /api/analysis-tools`` (built-in
tools + external viewers, each capability-matched).

Uses the ``inproc_client`` FACTORY fixture against a throwaway copy of the
ws_increase_demo fixture (see tests/test_api_analysis_tools.py).
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


def test_tab_has_no_h2_or_paragraph(inproc_client, ws_copy):
    client = inproc_client(workspace=ws_copy)
    html = client.get("/").text
    # the redundant in-page H2 and the descriptive paragraph are gone
    assert ">Analysis Tools</h2>" not in html
    assert "Interactive scenes saved as workspace artifacts" not in html


def test_analysis_tools_json_drives_cards(inproc_client, ws_copy):
    # the tools payload is the data source for the tab: the endpoint responds
    # with a "tools" list. Built-ins are capability-gated now, and this fixture
    # has no runs/packs, so parsimony-viewer (requires a 3d_pack) is correctly
    # absent rather than shown as a dead card — see test_api_analysis_tools and
    # test_analysis_tools.test_builtin_skipped_when_capability_absent.
    client = inproc_client(workspace=ws_copy)
    body = client.get("/api/analysis-tools").json()
    assert isinstance(body.get("tools"), list)
    assert not any(t["id"] == "parsimony-viewer" for t in body["tools"])

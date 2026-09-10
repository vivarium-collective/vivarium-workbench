"""UI: compatible-analysis-tools launch chips on the Simulations DB rows.

Cheap "the wiring exists" checks against the live FastAPI app (via
``dashboard_client``) and the served static JS/HTML, mirroring
tests/test_rerun_ui.py's style — not exhaustive JS behavior tests (this repo
has no JS execution harness for sim-table.js), just confirmation that the
frontend piece is actually wired up alongside the backend `matched_tools`
data (tests/test_simulations_matched_tools.py).
"""
from __future__ import annotations

import shutil
from pathlib import Path

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


def _ws_copy(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURE, ws)
    return ws


def test_sim_table_js_renders_matched_tools_chips(dashboard_client, tmp_path):
    client = dashboard_client(workspace=_ws_copy(tmp_path))
    r = client.get("/sim-table.js")
    assert r.status_code == 200
    assert "matched_tools" in r.text
    assert "tool-launch-btn" in r.text
    assert "toolsCell" in r.text


def test_index_html_has_redesigned_run_columns(dashboard_client, tmp_path):
    # The global Runs table was decluttered from 13 columns to 6
    # (Run · Config · Kind · Time · Status · Actions). The standalone Tools
    # column was dropped from the GLOBAL grid — matched-tool launchers remain in
    # the per-study Simulations tab (legacy layout) and the Analysis tab — so the
    # header no longer carries it.
    client = dashboard_client(workspace=_ws_copy(tmp_path))
    r = client.get("/")
    assert r.status_code == 200
    assert ">Run</th>" in r.text
    assert ">Kind</th>" in r.text
    assert ">Tools</th>" not in r.text

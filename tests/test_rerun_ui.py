"""UI: Rerun/Reproduce buttons (Task 7, updated by Task 4).

Thin smoke tests over the served HTML/JS for the Rerun/Reproduce affordances:

  1. Sim DB row action (``static/sim-table.js`` ``_actions``) — a per-row
     ``↻ Rerun`` control. reproducible-rerun-spine Task 4 repointed this at
     ``POST /api/study-reproduce`` (manifest replay) — "Reproduce" vs "Run
     current spec" is a deliberate, named distinction (see
     ``test_study_reproduce_button.py``). study-reproduce is the sole replay
     endpoint; the redundant generic ``/api/run-rerun`` was folded into it.
  2. Investigation header (``templates/index.html.j2``) — an
     ``#investigation-rerun`` button (Task 4 relabeled it "Run current spec"
     — it still re-derives from each member study's current study.yaml via
     ``/api/investigation-rerun``; an investigation-level "Reproduce" is
     explicitly out of Task 4's scope, left for Task 7's DAG-ordered work).
  3. Study-detail header — a "Rerun study" control (exercised indirectly via
     ``static/study-detail.js`` containing the wiring; the served-page
     assertion here focuses on the two pieces above, per the brief).

These are cheap "the wiring exists" checks against the live FastAPI app (via
``inproc_client``), not exhaustive behavioral tests — behavior of the
endpoints themselves is covered by ``test_api_rerun.py``.
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


def test_index_html_has_investigation_rerun_button(inproc_client, ws_copy):
    client = inproc_client(workspace=ws_copy)
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="investigation-rerun"' in r.text


def test_sim_table_js_has_rerun_action(inproc_client, ws_copy):
    client = inproc_client(workspace=ws_copy)
    r = client.get("/sim-table.js")
    assert r.status_code == 200
    # Task 4: the per-row control reproduces the recorded manifest via
    # /api/study-reproduce (the sole replay endpoint).
    assert "study-reproduce" in r.text
    assert "Rerun" in r.text


def test_sim_table_js_has_run_analysis_action(inproc_client, ws_copy):
    """Backlog item 23: a per-row control that fires the analysis phase on an
    EXISTING completed remote simulation. The original gap was found by grepping
    static/*.js for any such client-side code and finding NONE — so this asserts
    the wiring at the same level the gap was measured at: the button class, the
    endpoint it posts to, and the `data-remote-sim-id` attribute the delegated
    handler resolves the simulation id from (a button rendered without the
    attribute would silently no-op)."""
    client = inproc_client(workspace=ws_copy)
    r = client.get("/sim-table.js")
    assert r.status_code == 200
    assert "run-analysis-btn" in r.text
    assert "/api/remote-run-analysis" in r.text
    assert "data-remote-sim-id" in r.text
    # …and it polls the real status endpoint rather than declaring success on
    # submission (the analysis job pulls a multi-GB image before it even runs).
    assert "analysis_id=" in r.text

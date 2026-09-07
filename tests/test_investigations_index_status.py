"""The studies index (`GET /api/investigations`) reports a *display* status,
not the raw legacy ``status`` field: a stale ``status: running`` with no active
run must not read as "running" (it would mislabel the Studies-tab Status column
and pin the investigation to "Running now"). Only a genuine live run counts.
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from vivarium_workbench.lib.investigations_index import build_investigations


def _study(ws: Path, name: str, **fields) -> None:
    p = ws / "studies" / name / "study.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(
        {"schema_version": 3, "name": name,
         "baseline": [{"composite": "x", "name": "b"}], **fields},
        sort_keys=False,
    ))


def _rows(ws: Path) -> dict:
    return {r["name"]: r for r in build_investigations(ws)["investigations"]}


def test_studies_index_demotes_stale_running(tmp_path):
    # Stale legacy `status: running`, no live run; real state is gate 'blocked'.
    ws = tmp_path / "ws"
    _study(ws, "s1", status="running", gate_status="blocked")
    assert _rows(ws)["s1"]["status"] == "blocked"


def test_studies_index_stale_running_no_axis_falls_to_planning(tmp_path):
    # Stale running with no multi-axis fallback → planning, never "running".
    ws = tmp_path / "ws"
    _study(ws, "s1", status="running")
    assert _rows(ws)["s1"]["status"] == "planning"


def test_studies_index_running_with_active_run(tmp_path):
    # A genuine active run (running row + fresh heartbeat) IS "running".
    ws = tmp_path / "ws"
    _study(ws, "s2", status="running",
           runs=[{"kind": "simulation", "status": "running", "heartbeat_at": time.time()}])
    assert _rows(ws)["s2"]["status"] == "running"


# --- rail/card status sync: the flat studies index must carry the SAME status
# axes the investigation-graph nodes do, so the sidebar dot and the graph card
# (both fed into the client's single _studyStatusMeta) can never disagree. Was:
# the index dropped confidence/gate_status/effective_status, so a study whose
# graph card read "Investigating" (from confidence) showed "Planned" (blue) in
# the rail. See build_investigations + build_iset_detail. ---

_SYNC_AXES = (
    "effective_status", "gate_status", "confidence",
    "simulation_status", "evaluation_status",
)


def test_studies_index_carries_all_status_axes(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "s1", status="designed", confidence="Investigating")
    row = _rows(ws)["s1"]
    for axis in _SYNC_AXES:
        assert axis in row, f"flat studies index dropped {axis!r} (rail/card desync)"


def test_studies_index_surfaces_confidence(tmp_path):
    # The CD2 case: a `confidence` field is the only thing making the graph card
    # "Investigating" — the index must carry it so the rail agrees.
    ws = tmp_path / "ws"
    _study(ws, "s1", status="designed", confidence="Investigating")
    assert _rows(ws)["s1"]["confidence"] == "Investigating"


def test_studies_index_effective_status_is_multi_axis(tmp_path):
    # The mbp case: legacy `status: planned` but `evaluation_status: evaluated`.
    # effective_status must fold the multi-axis truth (evaluated), not echo the
    # stale legacy status — otherwise the study reads "Planned" in the graph card.
    ws = tmp_path / "ws"
    _study(ws, "s1", status="planned",
           simulation_status="complete", evaluation_status="evaluated")
    assert _rows(ws)["s1"]["effective_status"] == "evaluated"

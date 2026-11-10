"""Regression: the study-detail spec must carry the SAME run-aware execution_status the
investigation-graph node computes, or the two views diverge (graph "Completed" vs page
"Planned"). load_study_detail_spec previously omitted execution_status, so the detail page's
client fell through to the hard-coded "planned" default even for a study that had runs.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from vivarium_workbench.lib.study_spec import load_study_detail_spec
from vivarium_workbench.lib.study_ontology import derive_execution_status


def _make_runs_db(path: Path, slug: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT, spec_id TEXT, label TEXT, "
        "params_json TEXT, started_at REAL, completed_at REAL, n_steps INTEGER, "
        "status TEXT, sim_name TEXT, generation_id TEXT)"
    )
    conn.execute(
        "INSERT INTO runs_meta VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("db-run-1", slug, "DB Run", '{"seed": 0}', 1700000000.0, 1700000010.0,
         100, "completed", "baseline", None),
    )
    conn.commit()
    conn.close()


def _make_ws(tmp_path: Path) -> Path:
    sd = tmp_path / "studies" / "ran-study"
    sd.mkdir(parents=True)
    # A study that RAN (runs.db below) + was evaluated (verdict/finding), but carries NO
    # authored execution_status and no legacy status axes — exactly the shape that tripped
    # the divergence.
    (sd / "study.yaml").write_text(yaml.dump({
        "name": "ran-study",
        "composite": "pbg_ws.composites.baseline",
        "runs": [],
        "simulation_set": [{"name": "baseline", "is_baseline": True, "status": "ready"}],
        "verdict": "supported",
        "findings": [{"id": "f", "statement": "x", "status": "confirms"}],
        "behavior_tests": [{"name": "t", "measure": {"kind": "generation_average"},
                            "pass_if": {"op": ">=", "value": 0.0}}],
    }), encoding="utf-8")
    _make_runs_db(sd / "runs.db", "ran-study")
    inv = tmp_path / "investigations" / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        yaml.dump({"name": "inv", "title": "Inv", "studies": ["ran-study"]}), encoding="utf-8")
    return tmp_path


def test_detail_spec_carries_runaware_execution_status(tmp_path):
    ws = _make_ws(tmp_path)
    detail = load_study_detail_spec(ws, "ran-study")
    assert detail is not None
    # regression: the field is present (it was omitted before, causing the page to default to planned)
    assert "execution_status" in detail
    # single source of truth: it equals the graph node's derivation over the same runs
    runs = detail.get("runs") or []
    assert detail["execution_status"] == derive_execution_status(detail, runs=runs)
    # run-aware: a study with a completed run is NOT "planned" (the old buggy page value)
    assert detail["execution_status"] != "planned"


def test_authored_execution_status_is_honored(tmp_path):
    # An explicitly authored execution_status must survive (derive_execution_status honors it).
    ws = _make_ws(tmp_path)
    sp = ws / "studies" / "ran-study" / "study.yaml"
    d = yaml.safe_load(sp.read_text()); d["execution_status"] = "completed"
    sp.write_text(yaml.dump(d), encoding="utf-8")
    detail = load_study_detail_spec(ws, "ran-study")
    assert detail["execution_status"] == "completed"

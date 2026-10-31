"""Study / investigation status markings reflect ACTUAL runs (runs.db), not just
authored YAML. A study with a completed run but no authored execution axis used
to read "Planned" on the card/graph/roll-up; the run-derived simulation/evaluation
status (study_status.derive_status) must now flow in, while an authored value
the human set still wins."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from vivarium_workbench.lib.investigation_status import build_iset_summary
from vivarium_workbench.lib.investigations_index import (
    _condition_satisfied, build_investigations,
)
from vivarium_workbench.lib.report_views import build_iset_detail
from vivarium_workbench.lib.study_ontology import derive_execution_status


def _study(ws: Path, name: str, **fields) -> None:
    p = ws / "studies" / name / "study.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(
        {"schema_version": 3, "name": name,
         "baseline": [{"composite": "x", "name": "b"}], **fields},
        sort_keys=False))


def _runs_db(ws: Path, name: str, status: str = "completed") -> None:
    conn = sqlite3.connect(str(ws / "studies" / name / "runs.db"))
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, "
        "label TEXT, params_json TEXT, started_at REAL NOT NULL, completed_at REAL, "
        "n_steps INTEGER, status TEXT NOT NULL, sim_name TEXT)")
    conn.execute("INSERT INTO runs_meta VALUES ('r1','spec','lbl','{}',1.0,2.0,10,?,'s')",
                 (status,))
    conn.commit()
    conn.close()


def _inv(ws: Path, name: str, studies: list[str]) -> None:
    p = ws / "investigations" / name / "investigation.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"name": name, "title": name, "studies": studies}))


def _row(ws, name):
    return {r["name"]: r for r in build_investigations(ws)["investigations"]}[name]


def test_completed_run_without_authored_axis_reads_completed_at_card_level(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "s1")                       # no authored axis at all
    _runs_db(ws, "s1", "completed")
    _study(ws, "s2")                       # control: never ran
    _inv(ws, "inv", ["s1"])

    row = _row(ws, "s1")
    assert row["execution_status"] == "completed"
    assert row["status"] == "ran"          # was "planning"
    assert _row(ws, "s2")["execution_status"] == "planned"

    # investigation detail: study card Progress + header roll-up agree with it
    detail = build_iset_detail(ws, "inv")
    s1 = detail["studies"][0]
    assert s1["execution_status"] == "completed"
    assert detail["effective_status"] == "complete"
    # summaries (cards) roll-up
    summ = {i["name"]: i for i in build_iset_summary(ws, study_has_runs=lambda *a: True)}
    assert summ["inv"]["effective_status"] == "complete"


def test_authored_axis_still_wins_over_runs(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "s1", execution_status="planned")      # human said planned
    _runs_db(ws, "s1", "completed")
    assert _row(ws, "s1")["execution_status"] == "planned"
    _study(ws, "s2", simulation_status="failed")
    _runs_db(ws, "s2", "completed")
    assert derive_execution_status({"simulation_status": "failed"},
                                   runs=[{"status": "completed"}]) == "failed"


def test_weak_authored_axis_defers_to_runs():
    spec = {"simulation_status": "not_run", "status": "planned"}
    assert derive_execution_status(spec) == "planned"
    assert derive_execution_status(spec, runs=[{"status": "completed"}]) == "completed"
    assert derive_execution_status(spec, runs=[{"status": "failed"}]) == "planned"


def test_condition_satisfied_uses_runs(tmp_path):
    parent = {"name": "p"}
    assert not _condition_satisfied(parent, "ran")
    assert _condition_satisfied(parent, "ran", runs=[{"status": "completed"}])
    assert _condition_satisfied(parent, "complete", runs=[{"status": "ran"}])


def test_child_not_blocked_when_parent_ran(tmp_path):
    ws = tmp_path / "ws"
    _study(ws, "parent")
    _runs_db(ws, "parent", "completed")
    _study(ws, "child", parent_studies=[{"study": "parent", "condition": "ran"}])
    assert _row(ws, "child")["blocked"] is False

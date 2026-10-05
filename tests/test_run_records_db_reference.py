"""Run records read from a study's ``runs.db`` say where their data is, so the rigor scorecard credits them (#1288).

``viva_superpowers.rigor`` counts a run as persisted only if its record carries an emitter or a run-db reference
(``db_path`` ...). The records the workbench builds from ``runs.db`` carried neither, so a study whose trajectories
were saved scored ``run_persistence: gap``. These tests build a REAL ``runs.db`` and ask the REAL scorecard.
"""
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import rigor_views, study_spec

pytest.importorskip("viva_superpowers.rigor")
from viva_superpowers.rigor import run_is_emitter_backed  # noqa: E402

SLUG = "bench"


def make_study(ws: Path, *, runs_meta: int = 2, simulations_only: int = 1, jsonl_only: int = 0, yaml_only: int = 0) -> Path:
    sd = ws / "studies" / SLUG
    sd.mkdir(parents=True)
    spec: dict = {"name": SLUG, "schema_version": 4, "question": "q", "hypothesis": "h",
                  "baseline": [{"name": "b", "composite": "pkg.composites.c", "params": {}}]}
    if yaml_only:
        spec["runs"] = [{"run_id": f"y{i}", "label": f"yaml-{i}", "status": "completed"} for i in range(yaml_only)]
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    if runs_meta or simulations_only:
        con = sqlite3.connect(sd / "runs.db")
        con.execute("CREATE TABLE runs_meta (run_id TEXT, spec_id TEXT, label TEXT, params_json TEXT, started_at REAL, "
                    "completed_at REAL, n_steps INTEGER, status TEXT, sim_name TEXT)")
        con.execute("CREATE TABLE simulations (simulation_id TEXT, name TEXT, started_at REAL, completed_at REAL)")
        for i in range(runs_meta):
            con.execute("INSERT INTO runs_meta VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"r{i}", "pkg.composites.c", f"run {i}", json.dumps({"composite": "pkg.composites.c"}),
                         1.0 + i, 2.0 + i, 10, "completed", f"run {i}"))
        for i in range(simulations_only):
            con.execute("INSERT INTO simulations VALUES (?,?,?,?)", (f"s{i}", None, 0.5, None))
        con.commit()
        con.close()
    if jsonl_only:
        (ws / ".pbg").mkdir(exist_ok=True)
        (ws / ".pbg" / "runs.jsonl").write_text("".join(
            json.dumps({"run_id": f"j{i}", "study_slug": SLUG, "spec_id": "c", "status": "completed",
                        "started_at": "2026-10-01T00:00:00Z"}) + "\n" for i in range(jsonl_only)))
    return sd


def test_records_from_runs_db_carry_a_workspace_relative_db_reference(tmp_path):
    make_study(tmp_path)
    rows = study_spec.read_runs_db_for_study(tmp_path, SLUG)
    by_source = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)
    assert set(by_source) == {"runs_meta", "simulations"}
    for r in rows:
        assert r["db_path"] == f"studies/{SLUG}/runs.db", r["source"]
        assert not Path(r["db_path"]).is_absolute() and run_is_emitter_backed(r)


def test_the_real_scorecard_says_ok_for_runs_that_are_in_the_database(tmp_path):
    make_study(tmp_path, runs_meta=3, simulations_only=2)
    dim = {d["id"]: d for d in rigor_views.build_study_rigor(tmp_path, SLUG)["dimensions"]}["run_persistence"]
    assert dim["severity"] == "ok" and "5/5" in dim["detail"], dim


def test_runs_that_are_not_in_a_database_still_score_a_gap(tmp_path):
    """No runs.db, only study.yaml / runs.jsonl records: nothing evidences persistence, so the gap stays."""
    make_study(tmp_path, runs_meta=0, simulations_only=0, yaml_only=2, jsonl_only=1)
    rows = study_spec.read_runs_db_for_study(tmp_path, SLUG)
    assert rows and all("db_path" not in r or not r["db_path"] for r in rows)
    dim = {d["id"]: d for d in rigor_views.build_study_rigor(tmp_path, SLUG)["dimensions"]}["run_persistence"]
    assert dim["severity"] == "gap" and "none carry an emitter" in dim["detail"], dim


def test_a_mix_is_credited_only_for_the_database_runs(tmp_path):
    make_study(tmp_path, runs_meta=2, simulations_only=0, yaml_only=1, jsonl_only=1)
    rows = study_spec.read_runs_db_for_study(tmp_path, SLUG)
    persisted = [r for r in rows if run_is_emitter_backed(r)]
    assert len(persisted) == 2 and {r["source"] for r in persisted} == {"runs_meta"}
    assert len(rows) == 4

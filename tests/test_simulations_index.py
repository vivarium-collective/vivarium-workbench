"""Unit tests for vivarium_dashboard.lib.simulations_index."""
from pathlib import Path

import yaml

from vivarium_dashboard.lib.composite_runs import connect, save_metadata
from vivarium_dashboard.lib.simulations_index import list_simulations


def _seed_run(db_file, *, spec_id, run_id, started_at, sim_name=None):
    conn = connect(db_file)
    save_metadata(conn, spec_id=spec_id, run_id=run_id, params={}, label="",
                  started_at=started_at, n_steps=3, log_path=None)
    if sim_name:
        conn.execute("UPDATE runs_meta SET sim_name=? WHERE run_id=?",
                     (sim_name, run_id))
        conn.commit()
    conn.close()


def test_list_walks_workspace_and_studies_dbs(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    (ws / "studies" / "foo").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="r-scratch", started_at=10.0)
    _seed_run(ws / "studies" / "foo" / "runs.db",
              spec_id="pkg.y", run_id="r-baseline", started_at=20.0,
              sim_name="baseline")

    sims = list_simulations(ws)
    ids = [s["run_id"] for s in sims]
    assert ids == ["r-baseline", "r-scratch"]   # newest first
    assert sims[0]["db_path"] == "studies/foo/runs.db"
    assert sims[1]["db_path"] == ".pbg/composite-runs.db"
    assert sims[0]["sim_name"] == "baseline"
    # No study.yaml yet → empty studies annotation
    assert all(s["studies"] == [] for s in sims)


def test_list_cross_references_study_yaml_list_form(tmp_path):
    ws = tmp_path / "ws"
    (ws / "studies" / "foo").mkdir(parents=True)
    _seed_run(ws / "studies" / "foo" / "runs.db",
              spec_id="pkg.y", run_id="r-1", started_at=1.0)
    (ws / "studies" / "foo" / "study.yaml").write_text(
        yaml.safe_dump({"name": "foo", "runs": ["r-1"]}))

    sims = list_simulations(ws)
    assert len(sims) == 1
    assert sims[0]["studies"] == ["foo"]


def test_list_cross_references_study_yaml_dict_form(tmp_path):
    ws = tmp_path / "ws"
    (ws / "studies" / "foo").mkdir(parents=True)
    _seed_run(ws / "studies" / "foo" / "runs.db",
              spec_id="pkg.y", run_id="r-1", started_at=1.0)
    (ws / "studies" / "foo" / "study.yaml").write_text(
        yaml.safe_dump({"name": "foo",
                        "runs": [{"run_id": "r-1", "label": "baseline"}]}))

    sims = list_simulations(ws)
    assert sims[0]["studies"] == ["foo"]


def test_list_run_referenced_by_multiple_studies(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="shared", started_at=1.0)
    for name in ("alpha", "beta"):
        sdir = ws / "studies" / name
        sdir.mkdir(parents=True)
        (sdir / "study.yaml").write_text(
            yaml.safe_dump({"name": name, "runs": ["shared"]}))

    sims = list_simulations(ws)
    assert sims[0]["studies"] == ["alpha", "beta"]


def test_list_tolerates_missing_dbs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    # No .pbg/, no studies/ — should not raise
    assert list_simulations(ws) == []


def test_list_tolerates_malformed_study_yaml(tmp_path):
    ws = tmp_path / "ws"
    (ws / "studies" / "foo").mkdir(parents=True)
    _seed_run(ws / "studies" / "foo" / "runs.db",
              spec_id="pkg.y", run_id="r-1", started_at=1.0)
    (ws / "studies" / "foo" / "study.yaml").write_text("not: [valid: yaml")

    sims = list_simulations(ws)
    # The run still shows up; studies annotation is empty (yaml unparseable)
    assert len(sims) == 1
    assert sims[0]["studies"] == []


import os

from vivarium_dashboard.lib.simulations_index import (
    delete_simulation, RunNotFound,
)


def _write_history_row(db_file, simulation_id, step):
    """Seed one row in the SQLiteEmitter-owned history table."""
    import sqlite3
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history "
        "(simulation_id TEXT, step INTEGER, global_time REAL, state TEXT)")
    conn.execute(
        "INSERT INTO history (simulation_id, step, global_time, state) "
        "VALUES (?, ?, ?, ?)",
        (simulation_id, step, float(step), "{}"))
    conn.commit()
    conn.close()


def test_delete_full_pass(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".pbg" / "runs" / "r-1").mkdir(parents=True)
    (ws / ".pbg" / "runs" / "r-1" / "request.json").write_text("{}")
    db = ws / ".pbg" / "composite-runs.db"
    _seed_run(db, spec_id="pkg.x", run_id="r-1", started_at=1.0)
    _write_history_row(db, "r-1", 0)
    _write_history_row(db, "r-1", 1)
    # A study that references this run
    sdir = ws / "studies" / "alpha"
    sdir.mkdir(parents=True)
    (sdir / "study.yaml").write_text(
        yaml.safe_dump({"name": "alpha", "runs": ["r-1", "r-other"]}))

    summary = delete_simulation(ws, "r-1")
    assert summary["deleted_rows"] == 1
    assert summary["deleted_history"] == 2
    assert summary["removed_dir"] is True
    assert summary["unlinked_studies"] == ["alpha"]
    assert summary["errors"] == []

    # Listing now empty for this run
    assert list_simulations(ws) == []
    # study.yaml updated, other run preserved
    spec = yaml.safe_load((sdir / "study.yaml").read_text())
    assert spec["runs"] == ["r-other"]
    # Run dir gone
    assert not (ws / ".pbg" / "runs" / "r-1").exists()


def test_delete_unknown_raises(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    try:
        delete_simulation(ws, "ghost")
    except RunNotFound:
        return
    raise AssertionError("expected RunNotFound")


def test_delete_no_run_dir_no_studies(tmp_path):
    """Run lives only in DB — no run dir, no study refs. Clean delete."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="r-x", started_at=1.0)
    summary = delete_simulation(ws, "r-x")
    assert summary["deleted_rows"] == 1
    assert summary["removed_dir"] is False
    assert summary["unlinked_studies"] == []
    assert summary["errors"] == []


def test_delete_partial_failure_records_error(tmp_path):
    """A read-only study.yaml records an error but DB delete still succeeds."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="r-1", started_at=1.0)
    sdir = ws / "studies" / "alpha"
    sdir.mkdir(parents=True)
    yml = sdir / "study.yaml"
    yml.write_text(yaml.safe_dump({"name": "alpha", "runs": ["r-1"]}))
    # Make the file read-only AND its directory non-writable so atomic
    # write-then-rename fails (rename into a non-writable dir).
    os.chmod(sdir, 0o555)
    try:
        summary = delete_simulation(ws, "r-1")
        assert summary["deleted_rows"] == 1
        assert summary["errors"]   # some error recorded for alpha
        assert "alpha" in summary["errors"][0]
    finally:
        os.chmod(sdir, 0o755)   # restore so tmp_path cleanup works


def test_delete_dict_form_run_entry(tmp_path):
    """study.yaml runs[] can be list of dicts; delete removes the matching dict."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="r-1", started_at=1.0)
    sdir = ws / "studies" / "alpha"
    sdir.mkdir(parents=True)
    (sdir / "study.yaml").write_text(yaml.safe_dump({
        "name": "alpha",
        "runs": [{"run_id": "r-1", "label": "baseline"},
                 {"run_id": "r-other", "label": "v"}],
    }))
    delete_simulation(ws, "r-1")
    spec = yaml.safe_load((sdir / "study.yaml").read_text())
    assert spec["runs"] == [{"run_id": "r-other", "label": "v"}]

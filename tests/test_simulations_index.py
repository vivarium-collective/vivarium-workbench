"""Unit tests for vivarium_workbench.lib.simulations_index."""
import os
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib.composite_runs import connect, save_metadata
from vivarium_workbench.lib.simulations_index import (
    build_simulation_run_zip,
    list_simulations,
)


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


def test_db_path_is_workspace_root_relative_under_layout_remap(tmp_path):
    """A workspace that remaps ``studies`` to a subdir via ``workspace.yaml``
    ``layout:`` (e.g. ``studies: workspace/studies``) must still yield a
    db_path relative to the workspace ROOT, so the Data Explorer's
    ``read_source`` (``Path(workspace) / db_path``) resolves it.

    Regression: the discoverer hardcoded ``studies/<slug>`` and ignored the
    layout remap, so the Data Explorer looked at ``<ws>/studies/..`` (missing)
    instead of ``<ws>/workspace/studies/..`` and showed 0 runs.
    """
    from vivarium_workbench.lib.explorer_data import _resolve_run_source

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(yaml.safe_dump(
        {"layout": {"studies": "workspace/studies",
                    "investigations": "workspace/investigations"}}))
    study = ws / "workspace" / "studies" / "foo"
    study.mkdir(parents=True)
    _seed_run(study / "runs.db", spec_id="pkg.y", run_id="r-baseline",
              started_at=20.0)

    sims = list_simulations(ws)
    assert sims[0]["db_path"] == "workspace/studies/foo/runs.db"

    # read_source re-joins db_path against the workspace and must find the file.
    kind, resolved = _resolve_run_source(sims[0]["db_path"], ws)
    assert kind == "sqlite"
    assert resolved == ws / "workspace" / "studies" / "foo" / "runs.db"


def test_study_yaml_run_timestamp_maps_to_started_completed(tmp_path):
    """`record_runs` records a run's time as `timestamp` (not started_at/
    completed_at). The Simulations DB must surface it so the Time column isn't
    blank for study.yaml-recorded runs."""
    ws = tmp_path / "ws"
    (ws / "studies" / "foo").mkdir(parents=True)
    (ws / "studies" / "foo" / "study.yaml").write_text(yaml.safe_dump({
        "name": "foo",
        "runs": [{"name": "r-ts", "status": "completed",
                  "timestamp": 1234567890.0}],
    }))
    sims = list_simulations(ws)
    row = next(s for s in sims if s["run_id"] == "foo:r-ts")
    assert row["started_at"] == 1234567890.0
    assert row["completed_at"] == 1234567890.0


def test_list_discovers_nested_investigation_study_dbs(tmp_path):
    """Runs under the nested layout
    ``investigations/<inv>/studies/<slug>/runs.db`` must be discovered too —
    not just the root ``studies/<slug>/`` layout. Regression: nested-layout
    investigations (colonies, ketchup, pdmp) were entirely missing from the
    Simulations DB because the scanner only walked the root studies/ dir.
    """
    ws = tmp_path / "ws"
    nested = ws / "investigations" / "pdmp" / "studies" / "pdmp-01"
    nested.mkdir(parents=True)
    _seed_run(nested / "runs.db",
              spec_id="pkg.z", run_id="r-nested", started_at=30.0,
              sim_name="metabolism-ode")

    sims = list_simulations(ws)
    ids = [s["run_id"] for s in sims]
    assert "r-nested" in ids, sims
    row = next(s for s in sims if s["run_id"] == "r-nested")
    assert row["db_path"] == "investigations/pdmp/studies/pdmp-01/runs.db"


def test_list_tags_nested_study_yaml_runs_with_investigation(tmp_path):
    """A study.yaml-only run in a nested study dir surfaces and carries its
    investigation_slug derived from the path."""
    ws = tmp_path / "ws"
    nested = ws / "investigations" / "colonies" / "studies" / "colonies-02"
    nested.mkdir(parents=True)
    (nested / "study.yaml").write_text(yaml.safe_dump({
        "name": "colonies-02",
        "runs": [{"name": "perf-sweep", "status": "completed"}],
    }))

    sims = list_simulations(ws)
    row = next((s for s in sims if s["run_id"] == "colonies-02:perf-sweep"), None)
    assert row is not None, sims
    assert row["study_slug"] == "colonies-02"
    assert row["investigation_slug"] == "colonies"


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


def test_list_surfaces_emitterless_study_yaml_runs(tmp_path):
    """An emitter-less workspace (no runs.db / parquet / zarr) whose runs live
    only in study.yaml `runs:` (keyed by `name`, the numpy-investigation shape)
    must still surface in the Simulations DB — source 'study_yaml', associated
    to its study."""
    ws = tmp_path / "ws"
    (ws / "studies" / "loop").mkdir(parents=True)
    (ws / "studies" / "loop" / "study.yaml").write_text(yaml.safe_dump({
        "name": "loop",
        "runs": [{"name": "autopoiesis-meter", "status": "completed",
                  "composite": "pbg_autopoiesis.loop", "n_steps": 160,
                  "started_at": "2026-06-13T22:00:00Z"}],
    }))
    sims = list_simulations(ws)
    assert len(sims) == 1
    s = sims[0]
    assert s["run_id"] == "loop:autopoiesis-meter"
    assert s["source"] == "study_yaml"
    assert s["n_steps"] == 160
    assert s["studies"] == ["loop"]
    assert s["emitter"] == "none"


def test_runs_db_takes_priority_over_study_yaml_on_collision(tmp_path):
    """When the same run_id exists in both runs.db and study.yaml, the DB row
    (authoritative, with step history) wins; the study.yaml run does not
    duplicate it."""
    ws = tmp_path / "ws"
    (ws / "studies" / "foo").mkdir(parents=True)
    _seed_run(ws / "studies" / "foo" / "runs.db",
              spec_id="pkg.y", run_id="shared", started_at=5.0, sim_name="db-run")
    (ws / "studies" / "foo" / "study.yaml").write_text(yaml.safe_dump({
        "name": "foo",
        "runs": [{"name": "shared", "status": "completed", "n_steps": 99}],
    }))
    sims = list_simulations(ws)
    shared = [s for s in sims if s["run_id"] == "shared"]
    assert len(shared) == 1                       # not duplicated
    assert shared[0]["n_steps"] == 3              # DB data authoritative (not the spec's 99)


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


def _seed_sqlite_emitter_run(
    db_file,
    *,
    simulation_id,
    name,
    started_at_iso,
    study_slug=None,
    investigation_slug=None,
):
    """Seed a row in the SQLiteEmitter-shaped DB, with optional slug cols."""
    import sqlite3
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS simulations ("
        "  simulation_id TEXT PRIMARY KEY, name TEXT, started_at TEXT NOT NULL,"
        "  completed_at TEXT, elapsed_seconds REAL, composite_config TEXT,"
        "  metadata TEXT, emit_schema TEXT,"
        "  study_slug TEXT, investigation_slug TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history ("
        "  simulation_id TEXT, step INTEGER, global_time REAL, state TEXT)"
    )
    conn.execute(
        "INSERT INTO simulations "
        "(simulation_id, name, started_at, completed_at, study_slug, "
        "investigation_slug) VALUES (?, ?, ?, ?, ?, ?)",
        (simulation_id, name, started_at_iso, started_at_iso,
         study_slug, investigation_slug),
    )
    conn.execute(
        "INSERT INTO history (simulation_id, step, global_time, state) "
        "VALUES (?, ?, ?, ?)",
        (simulation_id, 0, 0.0, "{}"),
    )
    conn.commit()
    conn.close()


def test_list_exposes_study_and_investigation_slugs(tmp_path):
    """SQLiteEmitter rows with the new slug columns surface them in the API shape."""
    ws = tmp_path / "ws"
    db = ws / ".pbg" / "composite-runs.db"
    _seed_sqlite_emitter_run(
        db,
        simulation_id="sim-1",
        name="baseline-seed0",
        started_at_iso="2026-05-17T00:00:00Z",
        study_slug="dnaa-01-expression-dynamics",
        investigation_slug="dnaa-replication",
    )

    sims = list_simulations(ws)
    assert len(sims) == 1
    assert sims[0]["run_id"] == "sim-1"
    assert sims[0]["study_slug"] == "dnaa-01-expression-dynamics"
    assert sims[0]["investigation_slug"] == "dnaa-replication"


def test_list_tolerates_legacy_db_without_slug_columns(tmp_path):
    """SQLiteEmitter DBs predating the slug columns still list cleanly."""
    import sqlite3
    ws = tmp_path / "ws"
    db = ws / ".pbg" / "composite-runs.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    # Old shape — no study_slug / investigation_slug columns.
    conn.execute(
        "CREATE TABLE simulations ("
        "  simulation_id TEXT PRIMARY KEY, name TEXT, started_at TEXT NOT NULL,"
        "  completed_at TEXT, elapsed_seconds REAL)"
    )
    conn.execute(
        "CREATE TABLE history ("
        "  simulation_id TEXT, step INTEGER, global_time REAL, state TEXT)"
    )
    conn.execute(
        "INSERT INTO simulations (simulation_id, name, started_at, completed_at) "
        "VALUES (?, ?, ?, ?)",
        ("sim-legacy", "legacy", "2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z"),
    )
    conn.execute(
        "INSERT INTO history VALUES (?, ?, ?, ?)",
        ("sim-legacy", 0, 0.0, "{}"),
    )
    conn.commit()
    conn.close()

    sims = list_simulations(ws)
    assert len(sims) == 1
    assert sims[0]["run_id"] == "sim-legacy"
    assert sims[0]["study_slug"] is None
    assert sims[0]["investigation_slug"] is None


def test_list_derives_study_slug_from_path_for_legacy_per_study_db(tmp_path):
    """Per-study DBs without slug columns get a path-derived study_slug."""
    ws = tmp_path / "ws"
    db = ws / "studies" / "alpha" / "runs.db"
    _seed_sqlite_emitter_run(
        db,
        simulation_id="sim-perstudy",
        name="seed0",
        started_at_iso="2026-01-01T00:00:00Z",
    )

    sims = list_simulations(ws)
    assert len(sims) == 1
    # path -> studies, and study_slug falls back to studies[0]
    assert sims[0]["studies"] == ["alpha"]
    assert sims[0]["study_slug"] == "alpha"
    assert sims[0]["investigation_slug"] is None


def test_runs_meta_derives_study_slug_from_db_path(tmp_path):
    """A runs_meta row (remote / baseline run) at studies/<slug>/runs.db
    must surface study_slug == '<slug>' even without a study.yaml entry."""
    ws = tmp_path / "ws"
    (ws / "studies" / "my-study").mkdir(parents=True)
    _seed_run(ws / "studies" / "my-study" / "runs.db",
              spec_id="pkg.a", run_id="r-study-run", started_at=5.0)

    sims = list_simulations(ws)
    row = next((s for s in sims if s["run_id"] == "r-study-run"), None)
    assert row is not None, "run not found"
    assert row["study_slug"] == "my-study", f"expected 'my-study', got {row['study_slug']!r}"


def test_runs_meta_derives_study_slug_from_nested_db_path(tmp_path):
    """A runs_meta row nested at investigations/<inv>/studies/<slug>/runs.db
    must surface study_slug == '<slug>'."""
    ws = tmp_path / "ws"
    nested = ws / "investigations" / "inv-x" / "studies" / "nested-study"
    nested.mkdir(parents=True)
    _seed_run(nested / "runs.db",
              spec_id="pkg.b", run_id="r-nested-run", started_at=8.0)

    sims = list_simulations(ws)
    row = next((s for s in sims if s["run_id"] == "r-nested-run"), None)
    assert row is not None, "run not found"
    assert row["study_slug"] == "nested-study", f"expected 'nested-study', got {row['study_slug']!r}"


import os

from vivarium_workbench.lib.simulations_index import (
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


def _write_simulations_row(db_file, simulation_id):
    """Seed one row in the SQLiteEmitter-owned `simulations` index table."""
    import sqlite3
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS simulations "
        "(simulation_id TEXT, name TEXT, started_at TEXT, completed_at TEXT, "
        "elapsed_seconds REAL, composite_config TEXT, metadata TEXT)")
    conn.execute(
        "INSERT INTO simulations (simulation_id, name) VALUES (?, '')",
        (simulation_id,))
    conn.commit()
    conn.close()


def test_delete_clears_the_sqlite_simulations_index_row(tmp_path):
    """A run's row in the SQLiteEmitter's own `simulations` table must go too.

    Leaving it orphaned re-surfaced the run as a phantom "running" entry the
    Sim-DB fold could not shake (the reappearance this delete path prevents).
    """
    import sqlite3
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    db = ws / ".pbg" / "composite-runs.db"
    _seed_run(db, spec_id="pkg.x", run_id="r-1", started_at=1.0)
    _write_history_row(db, "r-1", 0)
    _write_simulations_row(db, "r-1")
    # A second run's rows must survive untouched.
    _seed_run(db, spec_id="pkg.x", run_id="r-keep", started_at=2.0)
    _write_simulations_row(db, "r-keep")

    delete_simulation(ws, "r-1")

    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute(
            "SELECT count(*) FROM simulations WHERE simulation_id='r-1'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM simulations WHERE simulation_id='r-keep'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_delete_tombstones_so_the_run_does_not_reappear(tmp_path):
    """After delete, the run must not re-synthesise from its `started` event.

    #554 folds the Sim-DB from the append-only JSONL log; delete_simulation
    tombstones the run so the next fold skips it. list_simulations backfills
    surviving DB rows into the log, so without the DB-row clear AND the
    tombstone the run comes back.
    """
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    db = ws / ".pbg" / "composite-runs.db"
    _seed_run(db, spec_id="pkg.x", run_id="r-ghost", started_at=1.0)
    assert any(r.get("run_id") == "r-ghost" for r in list_simulations(ws))

    delete_simulation(ws, "r-ghost")

    # A fresh fold (list_simulations backfills + folds) must not resurrect it.
    assert not any(r.get("run_id") == "r-ghost" for r in list_simulations(ws))


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


def test_delete_tolerates_non_dict_study_yaml(tmp_path):
    """study.yaml whose top-level is a list (or scalar) must not crash delete."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="r-1", started_at=1.0)
    sdir = ws / "studies" / "weird"
    sdir.mkdir(parents=True)
    # Top-level is a YAML list, not a dict — mimics a malformed/legacy file.
    (sdir / "study.yaml").write_text("- not a dict\n- still not a dict\n")

    summary = delete_simulation(ws, "r-1")
    assert summary["deleted_rows"] == 1
    assert summary["unlinked_studies"] == []   # nothing to unlink
    assert summary["errors"] == []             # tolerated, not error'd


# ---------------------------------------------------------------------------
# Parquet hive discovery (added 2026-05-27 for workspace-default parquet
# emitter parity — list_simulations now surfaces ParquetEmitter runs
# alongside the sqlite ones so the dashboard's Simulations tab covers
# both emitter generations).
# ---------------------------------------------------------------------------


def _write_parquet_hive(study_dir, experiment_id, *,
                        study_slug, investigation_slug,
                        n_history_rows=5, completed=True):
    """Build a minimal ParquetEmitter-shaped hive at
    ``<study_dir>/parquet-runs/<experiment_id>/``.

    Writes one row to configuration/ (metadata), n_history_rows to
    history/ (the captured trajectory), and an empty success/ subdir
    when ``completed=True`` so the scanner reads ``status=completed``.
    """
    pytest.importorskip("polars")  # parquet runs need polars (optional dep)
    import polars as pl
    exp = study_dir / "parquet-runs" / experiment_id
    # History — minimal columns the scanner reads (only the row count
    # matters; column shape mirrors what the runner emits).
    hive_part = (
        f"experiment_id={experiment_id}/variant=0/lineage_seed=0/"
        f"generation=1/agent_id=0"
    )
    history = exp / "history" / hive_part
    history.mkdir(parents=True)
    pl.DataFrame({
        "global_time": [float(i) for i in range(n_history_rows)],
        "listeners__mass__cell_mass": [1000.0 + i for i in range(n_history_rows)],
    }).write_parquet(history / "0.pq")
    # Configuration — the scanner reads study_slug + investigation_slug
    # from this dict (so its values can authoritatively override the
    # path-derived slug for cross-investigation references).
    config = exp / "configuration" / hive_part
    config.mkdir(parents=True)
    pl.DataFrame({
        "experiment_id":      [experiment_id],
        "variant":            [0],
        "lineage_seed":       [0],
        "generation":         ["1"],
        "agent_id":           ["0"],
        "study_slug":         [study_slug],
        "investigation_slug": [investigation_slug],
    }).write_parquet(config / "config.pq")
    # success/ marker — empty dir is enough; presence drives status="completed".
    if completed:
        (exp / "success" / hive_part).mkdir(parents=True)
    return exp


def test_list_surfaces_parquet_runs_alongside_sqlite(tmp_path):
    """A workspace with both sqlite + parquet runs surfaces both shapes
    via list_simulations under their respective `source` tags. This is
    the load-bearing test for the 2026-05-27 parquet migration."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    # One sqlite run via the existing helper.
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="sqlite-r1", started_at=1.0)
    # One parquet run.
    sdir = ws / "studies" / "mbp-02-population-aggregation"
    sdir.mkdir(parents=True)
    (sdir / "study.yaml").write_text(yaml.safe_dump({
        "name": "mbp-02-population-aggregation",
        "runs": [{
            "simulation":    "aggregator-cpa1-multigen",
            "simulation_id": "parquet-exp-1",
        }],
    }))
    _write_parquet_hive(
        sdir, "parquet-exp-1",
        study_slug="mbp-02-population-aggregation",
        investigation_slug="multiscale-bioprocess",
        n_history_rows=720,
    )

    sims = list_simulations(ws)
    # runs_meta rows don't carry a `source` field — _row_to_dict predates
    # the source tag; group untagged rows under "sqlite" for assertion.
    by_source: dict[str, list[dict]] = {}
    for s in sims:
        by_source.setdefault(s.get("source") or "sqlite", []).append(s)
    assert "sqlite" in by_source
    assert "parquet" in by_source
    parquet_rows = by_source["parquet"]
    assert len(parquet_rows) == 1
    p = parquet_rows[0]
    assert p["run_id"]              == "parquet-exp-1"
    assert p["sim_name"]            == "aggregator-cpa1-multigen"
    assert p["status"]              == "completed"
    assert p["n_steps"]             == 720
    assert p["study_slug"]          == "mbp-02-population-aggregation"
    assert p["investigation_slug"]  == "multiscale-bioprocess"
    assert p["db_path"].endswith("parquet-runs/parquet-exp-1")


def test_list_parquet_status_running_when_no_success_marker(tmp_path):
    """An in-progress parquet hive (no success/ subdir) surfaces as
    `status=running` so the dashboard's UI can render the spinner."""
    ws = tmp_path / "ws"
    sdir = ws / "studies" / "mbp-02-population-aggregation"
    sdir.mkdir(parents=True)
    _write_parquet_hive(
        sdir, "in-flight-exp",
        study_slug="mbp-02-population-aggregation",
        investigation_slug="multiscale-bioprocess",
        n_history_rows=42, completed=False,
    )
    sims = [s for s in list_simulations(ws) if s["source"] == "parquet"]
    assert len(sims) == 1
    assert sims[0]["status"] == "running"
    assert sims[0]["completed_at"] is None


def test_list_parquet_falls_back_to_experiment_id_when_no_study_yaml(tmp_path):
    """Cross-investigation pseudo-studies (parquet-runs/ dir but no
    study.yaml) still surface — sim_name falls back to the
    experiment_id."""
    ws = tmp_path / "ws"
    sdir = ws / "studies" / "multiscale-bioprocess-reference"
    sdir.mkdir(parents=True)
    # NO study.yaml written.
    _write_parquet_hive(
        sdir, "ref-exp-uuid",
        study_slug="multiscale-bioprocess-reference",
        investigation_slug="multiscale-bioprocess",
    )
    sims = [s for s in list_simulations(ws) if s["source"] == "parquet"]
    assert len(sims) == 1
    assert sims[0]["sim_name"] == "ref-exp-uuid"      # UUID fallback
    assert sims[0]["study_slug"] == "multiscale-bioprocess-reference"


def test_list_parquet_skips_hive_without_history_dir(tmp_path):
    """A parquet-runs/<exp>/ dir without a history/ subdir (in-progress
    write before first emit) is skipped, not surfaced as a broken row."""
    ws = tmp_path / "ws"
    sdir = ws / "studies" / "mbp-02-population-aggregation"
    sdir.mkdir(parents=True)
    # Build the experiment dir but NO history/ subdir.
    (sdir / "parquet-runs" / "skeletal-exp").mkdir(parents=True)
    sims = [s for s in list_simulations(ws) if s["source"] == "parquet"]
    assert sims == []


def _mk_hive_dirs(hive: Path):
    """Create the bare configuration/ + history/ + success/ marker dirs that
    identify a ParquetEmitter hive (no parquet files — discovery only checks
    for the dirs, so this stays polars-free)."""
    (hive / "configuration").mkdir(parents=True)
    (hive / "history").mkdir(parents=True)
    (hive / "success").mkdir(parents=True)


def test_discover_parquet_hives_finds_nested_and_flat(tmp_path):
    """The real ParquetEmitter/sweep output nests the hive below the run dir
    the user launched: ``parquet-runs/<run>/parquet/<experiment_id>/`` or
    ``parquet-runs/<run>/<inner>/``. Discovery must locate the hive at variable
    depth, key the row by the *run* dir (unique + user-meaningful), and still
    handle the flat ``parquet-runs/<run>/`` shape. Regression: previously only
    the flat depth-0 shape was found, so nested sweeps showed 0 runs.
    """
    from vivarium_workbench.lib.simulations_index import _discover_parquet_hives

    pr = tmp_path / "studies" / "s1" / "parquet-runs"
    # flat: hive IS the run dir
    _mk_hive_dirs(pr / "flat-run")
    # nested under parquet/: two distinct runs sharing one inner experiment id
    _mk_hive_dirs(pr / "sweep-a" / "parquet" / "equilibrium")
    _mk_hive_dirs(pr / "sweep-b" / "parquet" / "equilibrium")
    # nested one level under an arbitrarily-named inner dir
    _mk_hive_dirs(pr / "burnedin" / "repro")
    # a run dir with no hive anywhere → skipped
    (pr / "no-hive" / "run_seed0").mkdir(parents=True)

    found = _discover_parquet_hives(tmp_path)
    # one row per run dir that contains a hive (run dirs are unique keys)
    run_dirs = sorted(rd.name for (_hive, rd, _slug) in found)
    assert run_dirs == ["burnedin", "flat-run", "sweep-a", "sweep-b"]
    # each tuple's hive dir actually contains the hive markers
    for hive, _run, slug in found:
        assert (hive / "history").is_dir() and (hive / "configuration").is_dir()
        assert slug == "s1"


# ---------------------------------------------------------------------------
# remote_origin provenance (added 2026-06-20 for smsvpctest origin badge)
# ---------------------------------------------------------------------------

def _seed_remote_run(db_file, *, run_id, spec_id, simulation_id, source, started_at=1.0):
    """Seed a runs_meta row with remote-provenance params_json."""
    import json as _json
    conn = connect(db_file)
    provenance = {
        "simulation_id": simulation_id,
        "experiment_id": f"exp-{simulation_id}",
        "commit": "abc123",
        "backend": "ray",
        "source": source,
        "s3_uri": f"s3://bucket/prefix/{simulation_id}/",
        "store_path": str(db_file.parent / f"runs.{run_id}.zarr"),
    }
    save_metadata(conn, spec_id=spec_id, run_id=run_id, params=provenance,
                  label="Remote run", started_at=started_at, n_steps=0)
    conn.close()


def test_list_surfaces_remote_origin_from_params_json(tmp_path):
    """A runs_meta row with remote provenance params_json yields a non-None
    remote_origin dict on the row, with the deployment, simulation_id, etc."""
    ws = tmp_path / "ws"
    (ws / "studies" / "dnaa-01").mkdir(parents=True)
    db = ws / "studies" / "dnaa-01" / "runs.db"
    _seed_remote_run(db, run_id="r-remote", spec_id="pkg.x",
                     simulation_id=99, source="smsvpctest")

    sims = list_simulations(ws)
    assert len(sims) == 1
    row = sims[0]
    assert row["run_id"] == "r-remote"
    ro = row.get("remote_origin")
    assert ro is not None, f"expected remote_origin, got: {row}"
    assert ro["deployment"] == "smsvpctest"
    assert ro["simulation_id"] == 99
    assert ro["backend"] == "ray"
    assert ro["s3_uri"] == "s3://bucket/prefix/99/"
    # emitter derives from the landed .zarr store_path, NOT the runs.db (else "sqlite")
    assert row["emitter"] == "xarray"


def test_list_local_run_has_null_remote_origin(tmp_path):
    """A plain runs_meta row (no remote provenance) yields remote_origin=None."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    _seed_run(ws / ".pbg" / "composite-runs.db",
              spec_id="pkg.x", run_id="r-local", started_at=1.0)

    sims = list_simulations(ws)
    assert len(sims) == 1
    assert sims[0]["remote_origin"] is None


def test_empty_placeholder_dir_does_not_shadow_real_study(tmp_path):
    """A stale, empty ``investigations/<x>/studies/<slug>`` (no study.yaml /
    runs.db) must NOT shadow the real copy that holds the runs.db — the bug that
    made `basal` / `colonies-*` invisible in the Simulations DB."""
    ws = tmp_path / "ws"
    # Stale placeholder, iterated FIRST (alphabetically) — would win under the
    # old first-seen dedup.
    (ws / "investigations" / "aaa-stale" / "studies" / "basal").mkdir(parents=True)
    # Real copy with the actual runs.db.
    real = ws / "investigations" / "zzz-real" / "studies" / "basal"
    real.mkdir(parents=True)
    _seed_run(real / "runs.db", spec_id="s", run_id="basal-1", started_at=1.0)

    sims = {s["run_id"]: s for s in list_simulations(ws)}
    assert "basal-1" in sims                                   # real run discovered
    assert sims["basal-1"]["study_slug"] == "basal"
    # Scanned the real copy that holds the runs.db, not the stale placeholder.
    assert sims["basal-1"]["db_path"] == "investigations/zzz-real/studies/basal/runs.db"


def test_study_yaml_runs_with_simulation_id_keys_surface(tmp_path):
    """study.yaml runs recorded as ``{simulation, simulation_id}`` (multigen
    studies like mbp-*) must appear — not just the ``{name}`` / ``{run_id}`` forms."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "mbp-01"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(
        "name: mbp-01\n"
        "runs:\n"
        "  - simulation: static-env-baseline\n"
        "    simulation_id: abc-123\n"
        "    seed: 0\n"
    )
    sims = {s["run_id"]: s for s in list_simulations(ws)}
    assert "abc-123" in sims                       # keyed by simulation_id
    assert sims["abc-123"]["sim_name"] == "static-env-baseline"
    assert sims["abc-123"]["study_slug"] == "mbp-01"


# ---------------------------------------------------------------------------
# build_simulation_run_zip — epoch-0 mtimes (remote/S3-landed runs)
# ---------------------------------------------------------------------------


def _seed_run_with_store(db_file, *, run_id, store_path):
    """Like _seed_run, but records a store_path in the provenance JSON —
    what a landed remote run actually carries (see _row_to_dict)."""
    conn = connect(db_file)
    save_metadata(conn, spec_id="pkg.batch_baseline", run_id=run_id,
                  params={"store_path": store_path}, label="",
                  started_at=10.0, n_steps=3, log_path=None)
    conn.close()


def test_build_zip_handles_epoch_zero_mtime(tmp_path):
    """A file with mtime=0 (S3 downloads set no mtime — the actual shape of a
    landed remote run) must not crash the zip build with zipfile's bare
    'ZIP does not support timestamps before 1980' ValueError."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    store = ws / "runs.example.zarr"
    store.mkdir()
    f = store / "zarr.json"
    f.write_text("{}")
    os.utime(f, (0, 0))  # epoch 0 — exactly what a real landed run had

    _seed_run_with_store(ws / ".pbg" / "composite-runs.db",
                          run_id="r-remote", store_path="runs.example.zarr")

    data, filename, status = build_simulation_run_zip(ws, "r-remote")
    assert status == 200
    assert filename == "r-remote_emitter.zip"

    import zipfile
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["runs.example.zarr/zarr.json"]
        assert zf.read("runs.example.zarr/zarr.json") == b"{}"


def test_build_zip_preserves_normal_mtime(tmp_path):
    """A file with a normal (post-1980) mtime is written via the fast path
    (zf.write) unchanged — the fix only touches files that actually need it."""
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    store = ws / "runs.example.zarr"
    store.mkdir()
    (store / "zarr.json").write_text("{}")  # real, current mtime

    _seed_run_with_store(ws / ".pbg" / "composite-runs.db",
                          run_id="r-local", store_path="runs.example.zarr")

    data, filename, status = build_simulation_run_zip(ws, "r-local")
    assert status == 200

    import zipfile
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["runs.example.zarr/zarr.json"]


def test_build_zip_unknown_run_404s(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    data, filename, status = build_simulation_run_zip(ws, "no-such-run")
    assert status == 404
    assert data == b""


# ---------------------------------------------------------------------------
# build_simulation_run_zip — S3-only store falls back to sms-api (GovCloud
# runs whose local materialization no longer exists, e.g. after a pod restart)
# ---------------------------------------------------------------------------


def _remote_only_row(run_id, *, simulation_id, s3_uri):
    """A row shaped exactly like remote_simulations.py's _normalize() output —
    a run known to sms-api but never (or no longer) landed into a local
    runs.db, e.g. because the session/build checkout it landed into didn't
    survive a pod restart. db_path is None; store_path is the s3:// URI."""
    return {
        "run_id": run_id, "spec_id": "", "sim_name": run_id, "label": run_id,
        "status": "completed", "n_steps": None, "progress_step": None,
        "started_at": 10.0, "completed_at": 10.0,
        "db_path": None, "store_path": s3_uri, "emitter": "xarray",
        "studies": [], "study_slug": None, "investigation_slug": None,
        "remote_origin": {
            "deployment": "build #50", "simulation_id": simulation_id,
            "experiment_id": run_id, "backend": "aws", "s3_uri": s3_uri,
        },
    }


def test_build_zip_falls_back_to_sms_api_for_s3_only_store(tmp_path, monkeypatch):
    """A run whose store_path is an s3:// URI (nothing local to zip) — and
    whose remote_origin carries a simulation_id — must fetch the run's native
    store from sms-api (the same download_data() call remote_run_land already
    makes) instead of 404ing outright."""
    import tarfile

    from vivarium_workbench.lib import simulations_index as _si

    ws = tmp_path / "ws"
    row = _remote_only_row("r-s3-only", simulation_id=113,
                            s3_uri="s3://bucket/vecoli-output/exp-113")
    monkeypatch.setattr(_si, "list_simulations", lambda workspace: [row])

    # Build a fake tar.gz mirroring what sms-api's /data endpoint streams.
    tar_src = tmp_path / "fake_store"
    tar_src.mkdir()
    (tar_src / "zarr.json").write_text('{"ok": true}')
    tar_path = tmp_path / "fake.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(tar_src, arcname="exp-113")

    def _fake_download_data(self, simulation_id, dest_dir, timeout=None):
        assert simulation_id == 113
        out = Path(dest_dir) / f"sim_{simulation_id}.tar.gz"
        out.write_bytes(tar_path.read_bytes())
        return out

    monkeypatch.setattr(
        "vivarium_workbench.lib.sms_api_client.SmsApiClient.download_data",
        _fake_download_data,
    )

    data, filename, status = build_simulation_run_zip(ws, "r-s3-only")
    assert status == 200
    assert filename == "r-s3-only_emitter.zip"

    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert any(n.endswith("exp-113/zarr.json") for n in names), names
        matching = [n for n in names if n.endswith("exp-113/zarr.json")]
        assert zf.read(matching[0]) == b'{"ok": true}'


def test_build_zip_s3_only_store_with_no_remote_origin_404s(tmp_path, monkeypatch):
    """An s3:// store_path with no remote_origin (no simulation_id to fetch
    from) has nowhere left to resolve to — 404, not a crash."""
    from vivarium_workbench.lib import simulations_index as _si

    ws = tmp_path / "ws"
    row = _remote_only_row("r-s3-orphan", simulation_id=None, s3_uri="s3://bucket/orphan")
    row["remote_origin"] = None
    monkeypatch.setattr(_si, "list_simulations", lambda workspace: [row])

    data, filename, status = build_simulation_run_zip(ws, "r-s3-orphan")
    assert status == 404
    assert data == b""


def test_build_zip_sms_api_failure_404s_not_crashes(tmp_path, monkeypatch):
    """sms-api unreachable/erroring during the fallback fetch must fail closed
    (404), not raise SmsApiError up through the route handler."""
    from vivarium_workbench.lib import simulations_index as _si

    ws = tmp_path / "ws"
    row = _remote_only_row("r-s3-down", simulation_id=114,
                            s3_uri="s3://bucket/vecoli-output/exp-114")
    monkeypatch.setattr(_si, "list_simulations", lambda workspace: [row])

    def _raise(self, simulation_id, dest_dir, timeout=None):
        from vivarium_workbench.lib.sms_api_client import SmsApiError
        raise SmsApiError("sms-api unreachable")

    monkeypatch.setattr(
        "vivarium_workbench.lib.sms_api_client.SmsApiClient.download_data", _raise
    )

    data, filename, status = build_simulation_run_zip(ws, "r-s3-down")
    assert status == 404
    assert data == b""


def test_dict_name_runs_are_study_scoped_not_collapsed(tmp_path):
    """Two studies each recording a ``{name: baseline}`` run must surface as TWO
    distinct, study-scoped rows (``<slug>:baseline``) rather than collapsing into
    one on the shared name — the fix that lets every study's run appear in the
    Simulations DB. (Bare-string references to a shared run id still associate
    both studies; see test_list_run_referenced_by_multiple_studies.)"""
    ws = tmp_path / "ws"
    for name in ("study-a", "study-b"):
        sdir = ws / "studies" / name
        sdir.mkdir(parents=True)
        (sdir / "study.yaml").write_text(yaml.safe_dump(
            {"name": name, "runs": [{"name": "baseline", "status": "completed"}]}))
    sims = list_simulations(ws)
    by = {s["run_id"]: s.get("study_slug") for s in sims}
    assert set(by) == {"study-a:baseline", "study-b:baseline"}
    assert by["study-a:baseline"] == "study-a"
    assert by["study-b:baseline"] == "study-b"


def test_study_with_atlas_pack_advertises_capability(tmp_path):
    """A study that materialized viz/atlas/atlas.json advertises the ``atlas_pack``
    capability on its (store-less) run, so an atlas viewer requiring atlas_pack
    matches the run in the Tools column — output(pack) == input(viewer)."""
    from vivarium_workbench.lib import _root
    ws = tmp_path / "ws"
    sdir = ws / "studies" / "atlas-study"
    (sdir / "viz" / "atlas").mkdir(parents=True)
    (sdir / "viz" / "atlas" / "atlas.json").write_text("{}")
    (sdir / "study.yaml").write_text(yaml.safe_dump(
        {"name": "atlas-study", "runs": [{"name": "baseline", "status": "completed"}]}))
    _root.set_workspace_root(ws.resolve())
    sims = list_simulations(ws)
    row = next(s for s in sims if s.get("study_slug") == "atlas-study")
    assert "atlas_pack" in (row.get("capabilities") or [])


def test_runner_recorded_runs_not_prefixed_when_study_has_store(tmp_path):
    """A study with its own runs.db records real runs by their global id in each
    run's `name`; those must stay verbatim (merge with the store row), NOT get
    study-prefixed — otherwise running a study duplicates every row."""
    ws = tmp_path / "ws"
    sdir = ws / "studies" / "s"
    sdir.mkdir(parents=True)
    _seed_run(sdir / "runs.db", spec_id="pkg.x", run_id="run-abc", started_at=1.0)
    (sdir / "study.yaml").write_text(yaml.safe_dump(
        {"name": "s", "runs": [{"name": "run-abc", "status": "completed"}]}))
    sims = list_simulations(ws)
    ids = {s["run_id"] for s in sims}
    # one row, verbatim id (store row + study.yaml entry merged), no "s:run-abc"
    assert ids == {"run-abc"}


def test_sim_recency_key_pins_active_runs_to_top():
    """A just-launched (queued/running) cloud run must sort to the TOP of the
    merged Runs DB, ahead of older completed local/remote rows — the fix for a
    remote row being appended after the local sort and landing at the bottom."""
    from vivarium_workbench.lib.simulations_index import _sim_recency_key
    rows = [
        {"run_id": "local-old", "status": "completed", "started_at": 1788494400.0},
        {"run_id": "remote-queued", "status": "queued", "started_at": 1789416841.0,
         "remote_origin": {"simulation_id": 1343}},
        {"run_id": "remote-done", "status": "completed", "started_at": 1789000000.0,
         "remote_origin": {"simulation_id": 1300}},
        {"run_id": "local-recent", "status": "completed", "started_at": 1789500000.0},
    ]
    rows.sort(key=_sim_recency_key, reverse=True)
    assert rows[0]["run_id"] == "remote-queued"           # active pinned to top
    assert [r["run_id"] for r in rows[1:]] == ["local-recent", "remote-done", "local-old"]

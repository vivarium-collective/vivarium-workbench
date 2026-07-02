"""End-to-end test for POST /api/study-create-from-run."""
import sqlite3
import yaml
from pathlib import Path

import pytest


@pytest.fixture
def _ws_with_scratch_run(tmp_path):
    """Workspace with one completed test-run in .pbg/composite-runs.db."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: ws\ncreated: \"2026-05-13\"\nplugin_version: 0.6.1\npackage_path: pkg\n"
    )
    pbg = ws / ".pbg"
    pbg.mkdir()
    db = pbg / "composite-runs.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE runs_meta (
            run_id TEXT PRIMARY KEY,
            spec_id TEXT NOT NULL,
            label TEXT,
            params_json TEXT,
            started_at REAL NOT NULL,
            completed_at REAL,
            n_steps INTEGER,
            status TEXT NOT NULL
        );
        CREATE TABLE history (
            simulation_id TEXT NOT NULL,
            step INTEGER NOT NULL,
            global_time REAL,
            state TEXT NOT NULL,
            PRIMARY KEY (simulation_id, step)
        );
    """)
    conn.execute(
        "INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("rid1", "pkg.composites.foo", "test", "{}",
         1715620800.0, 1715620812.0, 10, "completed"),
    )
    conn.executemany(
        "INSERT INTO history VALUES (?, ?, ?, ?)",
        [("rid1", i, float(i), '{"x": ' + str(i) + '}') for i in range(10)],
    )
    conn.commit()
    conn.close()
    return ws


def test_create_from_run_writes_study_yaml(_ws_with_scratch_run):
    from vivarium_dashboard.lib.lifecycle_mutations import study_create_from_run as _post_study_create_from_run_for_test
    body = {
        "name": "my-study",
        "objective": "Why?",
        "description": "",
        "source_run_id": "rid1",
    }
    resp, code = _post_study_create_from_run_for_test(_ws_with_scratch_run, body)
    assert code == 200, resp
    sd = _ws_with_scratch_run / "studies" / "my-study"
    assert sd.is_dir()
    spec = yaml.safe_load((sd / "study.yaml").read_text())
    assert spec["schema_version"] == 3
    assert spec["name"] == "my-study"
    assert spec["objective"] == "Why?"
    assert spec["baseline"]["composite"] == "pkg.composites.foo"
    assert len(spec["runs"]) == 1
    assert spec["runs"][0]["run_id"] == "rid1"


def test_create_from_run_copies_history_rows(_ws_with_scratch_run):
    from vivarium_dashboard.lib.lifecycle_mutations import study_create_from_run as _post_study_create_from_run_for_test
    body = {"name": "my-study", "objective": "?",
            "description": "", "source_run_id": "rid1"}
    resp, code = _post_study_create_from_run_for_test(_ws_with_scratch_run, body)
    assert code == 200

    db = _ws_with_scratch_run / "studies" / "my-study" / "runs.db"
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT COUNT(*) FROM history WHERE simulation_id=?", ("rid1",)).fetchone()
    assert rows[0] == 10
    meta = conn.execute("SELECT run_id, spec_id FROM runs_meta").fetchall()
    assert meta == [("rid1", "pkg.composites.foo")]
    conn.close()


def test_create_from_run_leaves_scratch_untouched(_ws_with_scratch_run):
    from vivarium_dashboard.lib.lifecycle_mutations import study_create_from_run as _post_study_create_from_run_for_test
    body = {"name": "my-study", "objective": "?",
            "description": "", "source_run_id": "rid1"}
    _post_study_create_from_run_for_test(_ws_with_scratch_run, body)
    scratch = _ws_with_scratch_run / ".pbg" / "composite-runs.db"
    conn = sqlite3.connect(str(scratch))
    assert conn.execute("SELECT COUNT(*) FROM runs_meta").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 10
    conn.close()


def test_create_from_run_refuses_collision(_ws_with_scratch_run):
    from vivarium_dashboard.lib.lifecycle_mutations import study_create_from_run as _post_study_create_from_run_for_test
    body = {"name": "my-study", "objective": "?",
            "description": "", "source_run_id": "rid1"}
    _post_study_create_from_run_for_test(_ws_with_scratch_run, body)
    resp, code = _post_study_create_from_run_for_test(_ws_with_scratch_run, body)
    assert code == 409


def test_create_from_run_missing_source(_ws_with_scratch_run):
    from vivarium_dashboard.lib.lifecycle_mutations import study_create_from_run as _post_study_create_from_run_for_test
    body = {"name": "n", "objective": "?", "description": "", "source_run_id": "nope"}
    resp, code = _post_study_create_from_run_for_test(_ws_with_scratch_run, body)
    assert code == 404

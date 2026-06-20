import json
import sqlite3
from pathlib import Path

from vivarium_dashboard.lib import explorer_data


def make_fake_runs_db(db_path: Path, states: list[dict], run_id="run-1", name="baseline"):
    """Write a process_bigraph SQLiteEmitter-shaped runs.db with one run."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE simulations (
            simulation_id TEXT PRIMARY KEY, name TEXT,
            started_at TEXT, completed_at TEXT, elapsed_seconds REAL
        );
        CREATE TABLE history (
            simulation_id TEXT, step INTEGER, global_time REAL, state TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO simulations VALUES (?,?,?,?,?)",
        (run_id, name, "2026-01-01T00:00:00", "2026-01-01T00:01:00", 60.0),
    )
    for step, st in enumerate(states):
        conn.execute(
            "INSERT INTO history VALUES (?,?,?,?)",
            (run_id, step, float(step), json.dumps(st)),
        )
    conn.commit()
    conn.close()


def _sample_states(n=5):
    return [
        {
            "agents": {"0": {
                "listeners": {
                    "mass": {"cell_mass": 100.0 + i},
                    "fba_results": {"base_reaction_fluxes": [1.0 + i, 2.0 + i, 3.0 + i]},
                },
                "bulk": [["GLC", 10 + i], ["ATP", 20 + i]],
            }},
        }
        for i in range(n)
    ]


def test_list_runs_returns_run_dicts(tmp_path):
    studies = tmp_path / "studies" / "demo"
    studies.mkdir(parents=True)
    make_fake_runs_db(studies / "runs.db", _sample_states())
    runs = explorer_data.list_runs(tmp_path)
    assert isinstance(runs, list)
    assert any(r["run_id"] == "run-1" for r in runs)
    r = next(r for r in runs if r["run_id"] == "run-1")
    assert {"run_id", "label", "n_steps", "status", "db_path", "source"} <= set(r)


def test_list_runs_empty_workspace(tmp_path):
    assert explorer_data.list_runs(tmp_path) == []


def test_list_observables_groups_by_category(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states())
    obs = explorer_data.list_observables(str(db))
    cats = obs["categories"]
    # mass is a scalar leaf under listeners.mass.cell_mass
    assert any(o["path"].endswith("mass.cell_mass") for g in cats.values() for o in g)
    # fba_results.base_reaction_fluxes is a numeric vector
    flux = [o for g in cats.values() for o in g if "base_reaction_fluxes" in o["path"]]
    assert flux and flux[0]["kind"] == "vector"
    # bulk is a list-of-pairs; exposed with bracket-delimited paths under "Bulk molecules"
    assert "Bulk molecules" in cats
    bulk_obs = cats["Bulk molecules"]
    assert any(o["path"].startswith("bulk[") for o in bulk_obs)
    glc = next((o for o in bulk_obs if o["path"] == "bulk[GLC]"), None)
    assert glc is not None, "Expected bulk[GLC] observable"
    assert glc["kind"] == "bulk"


def test_bulk_id_with_dot_categorized(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, [{"agents": {"0": {"bulk": [["CPD-123.4", 7]]}}}])
    obs = explorer_data.list_observables(str(db))
    paths = [o["path"] for o in obs["categories"].get("Bulk molecules", [])]
    assert "bulk[CPD-123.4]" in paths

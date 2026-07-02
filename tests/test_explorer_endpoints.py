"""End-to-end smoke test for the explorer_data public API.

Exercises every function that backs an HTTP endpoint:
  list_runs, list_observables, get_series, load_flux_assets, get_flux.

Helpers are defined inline (the tests/ dir has no __init__.py, so cross-module
imports would resolve to the venv's unrelated 'tests' package).
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

from vivarium_workbench.lib import explorer_data


# ---------------------------------------------------------------------------
# Inline fixture helpers (mirror of test_explorer_data versions)
# ---------------------------------------------------------------------------

def _make_fake_runs_db(db_path: Path, states: list, run_id="run-1", name="baseline"):
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


# ---------------------------------------------------------------------------
# End-to-end test
# ---------------------------------------------------------------------------

def test_full_explorer_flow(tmp_path):
    # --- build a minimal workspace with one study run ---
    studies = tmp_path / "studies" / "demo"
    studies.mkdir(parents=True)
    db = studies / "runs.db"
    _make_fake_runs_db(db, _sample_states())

    # list_runs discovers the db and returns at least one run entry
    runs = explorer_data.list_runs(tmp_path)
    assert runs, "list_runs must return at least one run"
    assert any(r["run_id"] == "run-1" for r in runs)
    run = next(r for r in runs if r["run_id"] == "run-1")
    assert run["db_path"] is not None, "run must carry a db_path"

    # list_observables discovers scalar, vector, and bulk observables
    obs = explorer_data.list_observables(str(db))
    assert obs["categories"], "list_observables must return non-empty categories"
    all_obs = [o for g in obs["categories"].values() for o in g]
    paths = {o["path"] for o in all_obs}
    assert "listeners.mass.cell_mass" in paths, "scalar leaf must appear"
    assert any("base_reaction_fluxes" in p for p in paths), "vector leaf must appear"
    assert any(p.startswith("bulk[") for p in paths), "bulk molecules must appear"

    # get_series returns aligned time + named series for a scalar path
    ser = explorer_data.get_series(str(db), [("listeners.mass.cell_mass", None)])
    assert ser["time"], "get_series must return a non-empty time axis"
    assert "listeners.mass.cell_mass" in ser["series"], "scalar series must be present"
    assert len(ser["series"]["listeners.mass.cell_mass"]) == len(ser["time"])

    # get_series also works for a vector index (key uses #index suffix)
    ser2 = explorer_data.get_series(
        str(db), [("listeners.fba_results.base_reaction_fluxes", 0)]
    )
    key = "listeners.fba_results.base_reaction_fluxes#0"
    assert key in ser2["series"], "vector index series must use #index key"

    # get_series works for bulk molecules
    ser3 = explorer_data.get_series(str(db), [("bulk[GLC]", None)])
    assert "bulk[GLC]" in ser3["series"], "bulk molecule series must be present"

    # load_flux_assets returns (list, dict) — even when assets are absent the
    # return type contract must hold
    base, idmap = explorer_data.load_flux_assets()
    assert isinstance(base, list), "base_reaction_ids must be a list"
    assert isinstance(idmap, dict), "reaction_id_map must be a dict"

    # get_flux always returns the required envelope keys regardless of coverage
    flux = explorer_data.get_flux(str(db), 0, base, idmap)
    assert "fluxes" in flux, "get_flux result must have 'fluxes'"
    assert "coverage" in flux, "get_flux result must have 'coverage'"
    assert "step" in flux, "get_flux result must carry the requested step"
    assert isinstance(flux["fluxes"], dict)
    assert {"mapped", "total"} <= set(flux["coverage"])


def test_vector_flow(tmp_path):
    # Load test_explorer_data by file path to avoid collision with the venv's
    # unrelated 'tests' package (unum's tests/__init__.py requires nose).
    _spec = importlib.util.spec_from_file_location(
        "_ted", Path(__file__).parent / "test_explorer_data.py"
    )
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    make_fake_runs_db = _mod.make_fake_runs_db
    _sample_states = _mod._sample_states

    studies = tmp_path / "studies" / "demo"; studies.mkdir(parents=True)
    db = studies / "runs.db"
    make_fake_runs_db(db, _sample_states(n=4))
    res = explorer_data.get_vector(str(db),
        "listeners.fba_results.base_reaction_fluxes", step=1)
    assert res["values"] and res["ids"]

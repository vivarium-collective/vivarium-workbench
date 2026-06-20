import json
import sqlite3
from pathlib import Path

import pytest

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


def test_get_series_extracts_scalar_and_vector(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states(n=5))
    res = explorer_data.get_series(
        str(db),
        paths=[("listeners.mass.cell_mass", None),
               ("listeners.fba_results.base_reaction_fluxes", 1)],
        subsample=100,
    )
    assert len(res["time"]) == 5
    mass = res["series"]["listeners.mass.cell_mass"]
    assert mass == [100.0, 101.0, 102.0, 103.0, 104.0]
    flux1 = res["series"]["listeners.fba_results.base_reaction_fluxes#1"]
    assert flux1 == [2.0, 3.0, 4.0, 5.0, 6.0]


def test_get_series_extracts_bulk_pair(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states(n=5))
    res = explorer_data.get_series(str(db), paths=[("bulk[GLC]", None)], subsample=100)
    assert res["series"]["bulk[GLC]"] == [10.0, 11.0, 12.0, 13.0, 14.0]


def test_get_flux_remaps_to_bigg(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states(n=4))
    base_ids = ["RXN-A", "RXN-B", "RXN-C"]
    id_map = {"RXN-A": "PGI", "RXN-C": "PFK"}  # RXN-B intentionally unmapped
    res = explorer_data.get_flux(str(db), step=2, base_ids=base_ids, id_map=id_map)
    # state at step 2: base_reaction_fluxes == [3.0, 4.0, 5.0]
    assert res["fluxes"] == {"PGI": 3.0, "PFK": 5.0}
    assert res["coverage"] == {"mapped": 2, "total": 3}
    assert res["step"] == 2


def test_base_ids_from_run_reads_emitted_ids(tmp_path):
    db = tmp_path / "runs.db"
    st = {"agents": {"0": {"base_reaction_ids": ["RXN-A", "RXN-B", "RXN-C"],
          "listeners": {"fba_results": {"base_reaction_fluxes": [1.0, 2.0, 3.0]}}}}}
    make_fake_runs_db(db, [st, st])
    assert explorer_data.base_ids_from_run(str(db)) == ["RXN-A", "RXN-B", "RXN-C"]


def test_explorer_assets_are_valid_json():
    import vivarium_dashboard
    base = Path(vivarium_dashboard.__file__).parent / "static" / "explorer"
    for name in ("ecoli_core.map.json", "reaction_id_map.json", "base_reaction_ids.json"):
        p = base / name
        if not p.exists():
            pytest.skip(f"asset {name} not generated yet")
        json.loads(p.read_text())  # raises if invalid


# ---------------------------------------------------------------------------
# Zarr / XArrayEmitter tests
# ---------------------------------------------------------------------------

def make_fake_zarr(store_path, n_steps=4, n_rxn=3):
    import numpy as np
    import xarray as xr

    emit = list(range(n_steps))
    part = xr.Dataset({"time_gen=1": ("emitstep_gen=1", [float(s) for s in emit])})
    mass = xr.Dataset({"generation=1": ("emitstep_gen=1",
                       [100.0 + s for s in emit])})
    flux = xr.Dataset(
        {"generation=1": (("emitstep_gen=1", "id_base_reaction_fluxes"),
                          np.array([[1.0 + s, 2.0 + s, 3.0 + s] for s in emit]))},
        coords={"id_base_reaction_fluxes": ["RXN-A", "RXN-B", "RXN-C"][:n_rxn]})
    dt = xr.DataTree.from_dict({
        "experiment_id=e/variant=0/lineage_seed=0": part,
        "experiment_id=e/variant=0/lineage_seed=0/cell_mass": mass,
        "experiment_id=e/variant=0/lineage_seed=0/base_reaction_fluxes": flux,
    })
    dt.to_zarr(str(store_path), mode="w")


def test_zarr_resolver_and_observables(tmp_path):
    run = tmp_path / ".pbg" / "runs" / "r1"
    run.mkdir(parents=True)
    make_fake_zarr(run / "store.zarr")
    kind, resolved = explorer_data._resolve_run_source(".pbg/runs/r1", tmp_path)
    assert kind == "zarr" and resolved.name == "store.zarr"
    obs = explorer_data.list_observables(".pbg/runs/r1", workspace=tmp_path)
    paths = [o["path"] for g in obs["categories"].values() for o in g]
    assert "cell_mass" in paths and "base_reaction_fluxes" in paths


def test_zarr_series(tmp_path):
    run = tmp_path / ".pbg" / "runs" / "r1"
    run.mkdir(parents=True)
    make_fake_zarr(run / "store.zarr")
    res = explorer_data.get_series(".pbg/runs/r1", [("cell_mass", None)],
                                   workspace=tmp_path)
    assert res["series"]["cell_mass"] == [100.0, 101.0, 102.0, 103.0]


def test_zarr_flux(tmp_path):
    run = tmp_path / ".pbg" / "runs" / "r1"
    run.mkdir(parents=True)
    make_fake_zarr(run / "store.zarr")
    idmap = {"RXN-A": "PGI", "RXN-C": "PFK"}
    res = explorer_data.get_flux_auto(".pbg/runs/r1", step=2, id_map=idmap,
                                      workspace=tmp_path)
    assert res["fluxes"] == {"PGI": 3.0, "PFK": 5.0}
    assert res["coverage"]["total"] == 3

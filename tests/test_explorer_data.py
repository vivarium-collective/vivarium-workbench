import json
import sqlite3
from pathlib import Path

import pytest

from vivarium_workbench.lib import explorer_data


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


def test_get_flux_sums_colliding_reactions(tmp_path):
    # Two EcoCyc reactions mapping to the SAME BiGG reaction (e.g. gpmA/gpmM ->
    # PGM) must be SUMMED, not overwritten.
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states(n=4))
    base_ids = ["RXN-A", "RXN-B", "RXN-C"]
    id_map = {"RXN-A": "PGM", "RXN-C": "PGM"}  # both collide onto PGM
    res = explorer_data.get_flux(str(db), step=2, base_ids=base_ids, id_map=id_map)
    # state at step 2: base_reaction_fluxes == [3.0, 4.0, 5.0]; PGM = 3.0 + 5.0
    assert res["fluxes"] == {"PGM": 8.0}
    assert res["coverage"] == {"mapped": 1, "total": 3}


def test_get_flux_auto_merges_exchange_fluxes(tmp_path, monkeypatch):
    # environment exchange fluxes get mapped onto the map's EX_ reactions so
    # uptake/secretion (e.g. glucose EX_glc__D_e) is visible.
    monkeypatch.setattr(explorer_data, "_flux_assets_cache", (["RXN-A"], {}))
    monkeypatch.setattr(explorer_data, "_exchange_assets_cache",
                        (["GLC[p]", "OXYGEN-MOLECULE[p]"], {"GLC[p]": "EX_glc__D_e"}))
    st = {"agents": {"0": {"listeners": {"fba_results": {
        "base_reaction_fluxes": [3.0],
        "external_exchange_fluxes": [-4.5, 1.2]}}}}}
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, [st, st])
    out = explorer_data.get_flux_auto(str(db), 1, {"RXN-A": "PGI"}, "run-1", tmp_path)
    assert out["fluxes"]["PGI"] == 3.0               # internal reaction
    assert out["fluxes"]["EX_glc__D_e"] == -4.5       # glucose exchange (uptake)
    assert "EX_o2_e" not in out["fluxes"]             # O2 not in curated map -> skipped
    assert out["coverage"]["exchange"] == 1


def test_get_base_fluxes_keys_by_ecocyc_id(tmp_path, monkeypatch):
    # base fluxes keep native EcoCyc ids (no BiGG remap) so they can be grouped
    # by EcoCyc pathway — the full metabolism, transport included.
    monkeypatch.setattr(explorer_data, "_flux_assets_cache", ([], {}))
    st = {"agents": {"0": {
        "base_reaction_ids": ["RXN-A", "RXN-B", "RXN-C"],
        "listeners": {"fba_results": {"base_reaction_fluxes": [1.5, 0.0, -2.0]}}}}}
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, [st, st])
    out = explorer_data.get_base_fluxes(str(db), step=1, workspace=tmp_path)
    assert out["fluxes"] == {"RXN-A": 1.5, "RXN-B": 0.0, "RXN-C": -2.0}
    assert out["n"] == 3 and out["nonzero"] == 2


def test_base_ids_from_run_reads_emitted_ids(tmp_path):
    db = tmp_path / "runs.db"
    st = {"agents": {"0": {"base_reaction_ids": ["RXN-A", "RXN-B", "RXN-C"],
          "listeners": {"fba_results": {"base_reaction_fluxes": [1.0, 2.0, 3.0]}}}}}
    make_fake_runs_db(db, [st, st])
    assert explorer_data.base_ids_from_run(str(db)) == ["RXN-A", "RXN-B", "RXN-C"]


def test_explorer_assets_are_valid_json():
    import vivarium_workbench
    base = Path(vivarium_workbench.__file__).parent / "static" / "explorer"
    for name in ("ecoli_core.map.json", "reaction_id_map.json", "base_reaction_ids.json",
                 "pathways.json", "validation_proteomics.json", "explorer_labels.json"):
        p = base / name
        if not p.exists():
            pytest.skip(f"asset {name} not generated yet")
        json.loads(p.read_text())  # raises if invalid


def test_base_id_strips_compartment():
    assert explorer_data._base_id("AROG-MONOMER[c]") == "AROG-MONOMER"
    assert explorer_data._base_id("ALARACEBIOSYN-MONOMER") == "ALARACEBIOSYN-MONOMER"


def test_pick_even_subsamples():
    assert explorer_data._pick_even([0, 1, 2], 12) == [0, 1, 2]
    picked = explorer_data._pick_even(list(range(100)), 5)
    assert picked == [0, 25, 50, 74, 99]  # endpoints included, evenly spaced


def test_pearson_log10_perfect_correlation():
    pts = [{"sim": v, "exp": v} for v in (1, 10, 100, 1000)]
    assert explorer_data._pearson_log10(pts) == pytest.approx(1.0)
    assert explorer_data._pearson_log10(pts[:1]) is None  # < 2 points


def test_get_validation_scatter_averages_and_joins(tmp_path, monkeypatch):
    # 3-step run; monomer_counts is a 3-element vector (sqlite -> positional ids,
    # so the loader substitutes the aligned monomer-meta ids).
    states = [
        {"agents": {"0": {"listeners": {"monomer_counts": [2 + 2 * i, 99, 4 + 4 * i]}}}}
        for i in range(3)
    ]
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, states, run_id="r1")
    monkeypatch.setattr(explorer_data, "_monomer_ids_cache",
                        ["AAA-MONOMER", "BBB-MONOMER", "CCC-MONOMER"])
    monkeypatch.setattr(explorer_data, "_validation_cache", {
        "AAA-MONOMER": {"gene_name": "aaa", "monomer_name": "Aprot",
                        "schmidt": 10.0, "wisniewski": 5.0},
        "CCC-MONOMER": {"gene_name": "ccc", "monomer_name": "Cprot",
                        "schmidt": None, "wisniewski": 8.0},
    })
    out = explorer_data.get_validation_scatter(str(db), "schmidt", "r1", tmp_path, n_steps=3)
    by_id = {p["id"]: p for p in out["points"]}
    # AAA: avg over steps (2,4,6)=4, joined to schmidt=10
    assert by_id["AAA-MONOMER"]["sim"] == pytest.approx(4.0)
    assert by_id["AAA-MONOMER"]["exp"] == pytest.approx(10.0)
    assert by_id["AAA-MONOMER"]["gene"] == "aaa"
    # CCC has no schmidt value -> excluded from the schmidt scatter
    assert "CCC-MONOMER" not in by_id
    # BBB not in the validation set -> excluded
    assert "BBB-MONOMER" not in by_id

    # wisniewski dataset includes CCC (avg over (4,8,12)=8 joined to wisniewski=8)
    out_w = explorer_data.get_validation_scatter(str(db), "wisniewski", "r1", tmp_path, n_steps=3)
    by_id_w = {p["id"]: p for p in out_w["points"]}
    assert by_id_w["CCC-MONOMER"]["sim"] == pytest.approx(8.0)
    assert by_id_w["CCC-MONOMER"]["exp"] == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Zarr / XArrayEmitter tests
# ---------------------------------------------------------------------------

def make_fake_zarr(store_path, n_steps=4, n_rxn=3):
    pytest.importorskip("xarray")  # zarr tests need xarray (optional dep)
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


# ---------------------------------------------------------------------------
# Run-awareness: multi-sim runs.db
# ---------------------------------------------------------------------------

def _insert_second_sim(db_path: Path, states: list[dict], run_id: str, name: str):
    """Insert a second simulation into an existing runs.db (reuses its schema)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO simulations VALUES (?,?,?,?,?)",
        (run_id, name, "2026-01-02T00:00:00", "2026-01-02T00:01:00", 60.0),
    )
    for step, st in enumerate(states):
        conn.execute(
            "INSERT INTO history VALUES (?,?,?,?)",
            (run_id, step, float(step), json.dumps(st)),
        )
    conn.commit()
    conn.close()


def _states_with_mass(mass_base: float, n: int = 5):
    """Sample states where cell_mass starts at mass_base."""
    return [
        {"agents": {"0": {"listeners": {"mass": {"cell_mass": mass_base + i}}}}}
        for i in range(n)
    ]


def test_get_series_run_aware_multi_sim(tmp_path):
    """get_series honours run_id when the db contains multiple simulations."""
    db = tmp_path / "runs.db"
    # Run A: cell_mass 200..204
    make_fake_runs_db(db, _states_with_mass(200.0), run_id="A", name="run-a")
    # Run B: cell_mass 500..504  (inserted into the same file)
    _insert_second_sim(db, _states_with_mass(500.0), run_id="B", name="run-b")

    res_a = explorer_data.get_series(
        str(db), [("listeners.mass.cell_mass", None)], subsample=100, run_id="A"
    )
    res_b = explorer_data.get_series(
        str(db), [("listeners.mass.cell_mass", None)], subsample=100, run_id="B"
    )

    mass_a = res_a["series"]["listeners.mass.cell_mass"]
    mass_b = res_b["series"]["listeners.mass.cell_mass"]

    assert mass_a == [200.0, 201.0, 202.0, 203.0, 204.0], (
        f"Expected run A values (200-204), got {mass_a}"
    )
    assert mass_b == [500.0, 501.0, 502.0, 503.0, 504.0], (
        f"Expected run B values (500-504), got {mass_b}"
    )


def test_list_runs_excludes_empty_db(tmp_path):
    # A runs.db with a simulations row but ZERO history rows has nothing to
    # explore — it must not appear in the picker.
    studies = tmp_path / "studies" / "demo"
    studies.mkdir(parents=True)
    make_fake_runs_db(studies / "runs.db", [])
    assert explorer_data.list_runs(tmp_path) == []


def test_unit_and_class_helpers():
    assert explorer_data._unit_for("listeners.mass.protein_mass") == "fg"
    assert explorer_data._unit_for("listeners.mass.protein_mass_fraction") == ""
    assert explorer_data._unit_for("listeners.fba_results.base_reaction_fluxes") == "mmol·s⁻¹"
    assert explorer_data._unit_for("listeners.monomer_counts") == "counts"
    assert explorer_data._unit_for("bulk[GLC]") == "counts"
    assert explorer_data._mol_class("listeners.rna_counts.mRNA_counts") == "RNA"
    assert explorer_data._mol_class("listeners.monomer_counts") == "Protein"
    assert explorer_data._mol_class("bulk[GLC]") == "Metabolite"
    assert explorer_data._mol_class("listeners.fba_results.base_reaction_fluxes") == "Flux"
    assert explorer_data._mol_class("listeners.mass.cell_mass") == "Mass"


def test_list_observables_carries_unit_and_class(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states())
    obs = explorer_data.list_observables(str(db))
    flat = [o for g in obs["categories"].values() for o in g]
    assert flat and all("unit" in o and "mclass" in o for o in flat)
    mass = [o for o in flat if o["path"].endswith("mass.cell_mass")][0]
    assert mass["unit"] == "fg" and mass["mclass"] == "Mass"


def test_get_vector_sqlite_by_index(tmp_path):
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, _sample_states(n=4))
    # step 2 base_reaction_fluxes == [3.0, 4.0, 5.0]
    res = explorer_data.get_vector(str(db),
        "listeners.fba_results.base_reaction_fluxes", step=2)
    assert res["values"] == [3.0, 4.0, 5.0]
    assert res["ids"] == ["0", "1", "2"]


def test_get_vector_zarr_by_coord(tmp_path):
    run = tmp_path / ".pbg" / "runs" / "r1"; run.mkdir(parents=True)
    make_fake_zarr(run / "store.zarr")  # base_reaction_fluxes vector w/ id coord
    res = explorer_data.get_vector(".pbg/runs/r1", "base_reaction_fluxes",
                                   step=2, workspace=tmp_path)
    assert res["ids"] == ["RXN-A", "RXN-B", "RXN-C"]
    assert res["values"] == [3.0, 4.0, 5.0]


# ---------------------------------------------------------------------------
# Parquet / ParquetEmitter tests
# ---------------------------------------------------------------------------

def make_fake_parquet(root, variant=0, seed=0, n=4):
    """Write a minimal hive parquet run under root/exp/history/...

    Returns the lineage_seed directory (the 'db_path' for parquet runs).
    Skips (via pytest.importorskip) if pyarrow is unavailable.
    """
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")

    hist_dir = (root / "exp" / "history"
                / "experiment_id=exp" / f"variant={variant}"
                / f"lineage_seed={seed}" / "generation=1" / "agent_id=0")
    hist_dir.mkdir(parents=True)

    times = pa.array([float(i) for i in range(n)], type=pa.float64())
    cell_mass = pa.array([100.0 + i for i in range(n)], type=pa.float64())
    # 5 monomers per row, all rows identical values [0.0..4.0]
    monomer_data = [[float(j) for j in range(5)] for _ in range(n)]
    monomer_col = pa.array(monomer_data, type=pa.large_list(pa.float64()))
    bulk_id_col = pa.array([["GLC", "ATP"] for _ in range(n)],
                           type=pa.large_list(pa.large_string()))
    bulk_cnt_col = pa.array([[10 + i, 20 + i] for i in range(n)],
                            type=pa.large_list(pa.int64()))

    tbl = pa.table({
        "global_time": times,
        "listeners__mass__cell_mass": cell_mass,
        "listeners__monomer_counts": monomer_col,
        "bulk__id": bulk_id_col,
        "bulk__count": bulk_cnt_col,
    })
    pq.write_table(tbl, str(hist_dir / "0.pq"))

    # Config sidecar
    cfg_dir = (root / "exp" / "configuration"
               / "experiment_id=exp" / f"variant={variant}"
               / f"lineage_seed={seed}" / "generation=1" / "agent_id=0")
    cfg_dir.mkdir(parents=True)
    monomer_ids = [f"monomer_{j}" for j in range(5)]
    cfg_tbl = pa.table({
        "output_metadata__listeners__monomer_counts": pa.array(
            [monomer_ids], type=pa.large_list(pa.large_string())),
    })
    pq.write_table(cfg_tbl, str(cfg_dir / "config.pq"))

    return (root / "exp" / "history"
            / "experiment_id=exp" / f"variant={variant}" / f"lineage_seed={seed}")


def test_parquet_resolve_and_observables(tmp_path):
    pytest.importorskip("pyarrow")
    lineage_dir = make_fake_parquet(tmp_path)
    kind, resolved = explorer_data._resolve_run_source(str(lineage_dir))
    assert kind == "parquet"
    assert resolved == lineage_dir

    obs = explorer_data.list_observables(str(lineage_dir))
    cats = obs["categories"]
    flat = [o for g in cats.values() for o in g]
    paths = [o["path"] for o in flat]

    # cell_mass: scalar, Mass category
    assert "listeners__mass__cell_mass" in paths, f"paths={paths}"
    mass_obs = next(o for o in flat if o["path"] == "listeners__mass__cell_mass")
    assert mass_obs["kind"] == "scalar"
    assert mass_obs["unit"] == "fg"
    assert mass_obs["mclass"] == "Mass"

    # monomer_counts: vector, Protein
    assert "listeners__monomer_counts" in paths, f"paths={paths}"
    mc_obs = next(o for o in flat if o["path"] == "listeners__monomer_counts")
    assert mc_obs["kind"] == "vector"
    assert mc_obs["mclass"] == "Protein"


def test_parquet_series(tmp_path):
    pytest.importorskip("pyarrow")
    lineage_dir = make_fake_parquet(tmp_path, n=4)

    res = explorer_data.get_series(
        str(lineage_dir),
        paths=[("listeners__mass__cell_mass", None),
               ("listeners__monomer_counts", 1)],
        subsample=100,
    )
    assert len(res["time"]) == 4

    mass = res["series"]["listeners__mass__cell_mass"]
    assert mass == [100.0, 101.0, 102.0, 103.0], f"mass={mass}"

    # Index-1 of [0.0, 1.0, 2.0, 3.0, 4.0] is 1.0 for every row
    mc1 = res["series"]["listeners__monomer_counts#1"]
    assert mc1 == [1.0, 1.0, 1.0, 1.0], f"mc1={mc1}"


def test_parquet_vector(tmp_path):
    pytest.importorskip("pyarrow")
    lineage_dir = make_fake_parquet(tmp_path, n=4)

    res = explorer_data.get_vector(
        str(lineage_dir),
        "listeners__monomer_counts",
        step=2,
    )
    assert res["ids"] == [f"monomer_{j}" for j in range(5)], f"ids={res['ids']}"
    assert res["values"] == [0.0, 1.0, 2.0, 3.0, 4.0], f"values={res['values']}"
    assert res["step"] == 2


def test_get_protein_breakdown_groups_by_category(tmp_path, monkeypatch):
    # state carries a monomer_counts vector under agents/0/listeners
    states = [{"agents": {"0": {"listeners": {"monomer_counts": [10, 20, 30]}}}}]
    db = tmp_path / "runs.db"
    make_fake_runs_db(db, states)
    # fake per-monomer MW + category aligned to the vector
    monkeypatch.setattr(explorer_data, "_protein_meta_cache",
                        ([1.0, 2.0, 3.0], ["Enzyme", "Transport", "Enzyme"]))
    res = explorer_data.get_protein_breakdown(
        str(db), "listeners.monomer_counts", step=0)
    bd = res["breakdown"]
    assert bd["Enzyme"] == 10 * 1.0 + 30 * 3.0   # 100
    assert bd["Transport"] == 20 * 2.0           # 40

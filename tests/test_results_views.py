"""``lib.results_views.build_study_results`` (spec §3.3, plan Task 4):
per-store preview (sparkline + first/last/min/max) of a study's LATEST run,
resolved via the same ``simulations_index`` rows the Simulations panel uses,
read via the same store readers the Composite Explorer uses.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from vivarium_workbench.lib.results_views import build_study_results


def _make_fake_runs_db(db_path: Path, states: list[dict], run_id="run-1", name="baseline"):
    """A process_bigraph SQLiteEmitter-shaped runs.db with one run (mirrors
    ``tests/test_explorer_data.py``'s fixture writer)."""
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


def _sample_states(n=6):
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


def _study_runs_db(tmp_path: Path, slug="demo", **kwargs) -> Path:
    study_dir = tmp_path / "studies" / slug
    study_dir.mkdir(parents=True)
    db = study_dir / "runs.db"
    _make_fake_runs_db(db, _sample_states(), **kwargs)
    return db


def test_present_with_scalar_store_preview(tmp_path):
    _study_runs_db(tmp_path)
    payload, status = build_study_results(tmp_path, "demo")
    assert status == 200
    assert payload["present"] is True
    assert payload["run_id"] == "run-1"

    by_path = {s["path"]: s for s in payload["stores"]}
    key = "listeners.mass.cell_mass"
    assert key in by_path, f"expected {key!r} among {sorted(by_path)}"
    row = by_path[key]
    assert row["dtype"] == "float64"
    assert row["first"] == 100.0
    assert row["last"] == 105.0
    assert row["min"] == 100.0
    assert row["max"] == 105.0
    assert isinstance(row["sparkline"], list) and len(row["sparkline"]) > 0
    assert all(isinstance(v, (int, float)) for v in row["sparkline"])


def test_vector_and_bulk_leaves_excluded_from_scalar_preview(tmp_path):
    _study_runs_db(tmp_path)
    payload, _ = build_study_results(tmp_path, "demo")
    paths = {s["path"] for s in payload["stores"]}
    assert not any("base_reaction_fluxes" in p for p in paths), \
        "vector leaves should not appear in the scalar preview"
    assert not any(p.startswith("bulk[") for p in paths), \
        "bulk leaves should not appear in the scalar preview"


def test_sparkline_is_downsampled(tmp_path):
    """Preview only: a long-running store's sparkline stays bounded, never the
    full raw array."""
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    _make_fake_runs_db(study_dir / "runs.db", _sample_states(200))
    payload, _ = build_study_results(tmp_path, "demo")
    by_path = {s["path"]: s for s in payload["stores"]}
    row = by_path["listeners.mass.cell_mass"]
    assert len(row["sparkline"]) <= 30
    assert len(row["sparkline"]) < 200


def test_no_runs_graceful_empty(tmp_path):
    (tmp_path / "studies" / "demo").mkdir(parents=True)
    payload, status = build_study_results(tmp_path, "demo")
    assert status == 200
    assert payload == {"present": False, "reason": "no runs yet"}


def test_unknown_study_graceful_empty(tmp_path):
    payload, status = build_study_results(tmp_path, "no-such-study")
    assert status == 200
    assert payload["present"] is False


def test_invalid_slug_graceful_empty(tmp_path):
    payload, status = build_study_results(tmp_path, "Not A Slug!")
    assert status == 200
    assert payload == {"present": False, "reason": "invalid slug"}


def test_run_with_no_store_graceful_empty(tmp_path):
    """A study.yaml-declared run (emitter-less workspace convention — see
    ``simulations_index._read_study_yaml_runs``) with neither a ``store_path``
    nor a per-step ``runs.db`` carries ``db_path: None`` / ``store_path: None``
    — degrades gracefully instead of raising."""
    import yaml

    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({"name": "demo", "runs": [{"name": "baseline", "status": "completed"}]}),
        encoding="utf-8",
    )
    payload, status = build_study_results(tmp_path, "demo")
    assert status == 200
    assert payload["present"] is False
    assert payload.get("run_id") == "demo:baseline"


def _make_remote_run_db(db_path: Path, run_id="remote-run-1"):
    """A remote-run db as viva-biomodels imports it from the cluster: only
    ``runs_meta`` + ``history`` (no emitter ``simulations`` table), ONE
    snapshot step with a NULL ``global_time``, and a comparison-style state —
    a nested matrix of scalar numbers beside per-engine series lists."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE runs_meta (
            run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, label TEXT,
            params_json TEXT, started_at REAL NOT NULL, completed_at REAL,
            n_steps INTEGER, status TEXT NOT NULL, sim_name TEXT
        );
        CREATE TABLE history (
            simulation_id TEXT NOT NULL, step INTEGER NOT NULL,
            global_time REAL, state TEXT NOT NULL,
            PRIMARY KEY (simulation_id, step)
        );
        """
    )
    conn.execute(
        "INSERT INTO runs_meta VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, "spec", "Remote run", "{}", 1.0, 2.0, 1, "completed", "compare"),
    )
    state = {
        "metrics": {"M1": {"job": {"matrix": {
            "copasi": {"tellurium": 0.25},
            "tellurium": {"copasi": 0.25},
        }}}},
        "results": {"M1": {"job": {"copasi": {"time": [0.0, 1.0], "A": [1.0, 2.0]}}}},
    }
    conn.execute(
        "INSERT INTO history VALUES (?,?,?,?)", (run_id, 0, None, json.dumps(state))
    )
    conn.commit()
    conn.close()


def test_remote_snapshot_run_previews_comparison_scalars(tmp_path):
    """A remote run db has no ``simulations`` table and a NULL ``global_time``;
    its comparison scalars must still preview (the preview used to say
    'emitted no scalar observables' because the store reader bailed out)."""
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    _make_remote_run_db(study_dir / "runs.db")
    payload, status = build_study_results(tmp_path, "demo")
    assert status == 200
    assert payload["present"] is True
    by_path = {s["path"]: s for s in payload["stores"]}
    key = "metrics.M1.job.matrix.copasi.tellurium"
    assert key in by_path, f"expected {key!r} among {sorted(by_path)}"
    assert by_path[key]["first"] == by_path[key]["last"] == 0.25


# ---------------------------------------------------------------------------
# Store resolution: the preview reads whichever store the run ACTUALLY emitted
# to — not just the one path the simulations-index row recorded. A run can
# record a SQLite ``store_path`` (or a per-step ``runs.db``) while its scalar
# observables were emitted to the sibling XArray store ``<study>/runs.<run_id>
# .zarr`` (the XArrayEmitter convention). The index row then points the preview
# at the SQLite — which is empty of (or missing) those scalars — so the panel
# said "emitted no scalar observables" for runs that DID emit them.
# ---------------------------------------------------------------------------

def _make_fake_zarr(store_path: Path, leaf="enrichment_factor", n_steps=5):
    """A minimal XArrayEmitter-shaped zarr with one scalar leaf (mirrors
    ``tests/test_explorer_data.py``'s ``make_fake_zarr``)."""
    import pytest

    pytest.importorskip("xarray")  # zarr preview needs xarray (optional dep)
    import xarray as xr

    emit = list(range(n_steps))
    nodes = {
        "experiment_id=e/variant=0/lineage_seed=0":
            xr.Dataset({"time_gen=1": ("emitstep_gen=1", [float(s) for s in emit])}),
        f"experiment_id=e/variant=0/lineage_seed=0/{leaf}":
            xr.Dataset({"generation=1": ("emitstep_gen=1", [100.0 + s for s in emit])}),
    }
    xr.DataTree.from_dict(nodes).to_zarr(str(store_path), mode="w")


def _runs_meta_only_db(db_path: Path, run_id: str):
    """A study runs.db with a runs_meta row but NO history table — its scalar
    data lives in a sibling XArray store."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        "CREATE TABLE runs_meta(run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, "
        "label TEXT, params_json TEXT, started_at REAL NOT NULL, completed_at REAL, "
        "n_steps INTEGER, status TEXT NOT NULL, sim_name TEXT, generation_id TEXT, "
        "emitter_path TEXT, progress_step INTEGER, manifest_json TEXT, "
        "capabilities_json TEXT);"
    )
    conn.execute(
        "INSERT INTO runs_meta(run_id,spec_id,label,started_at,completed_at,"
        "n_steps,status,sim_name) VALUES(?,?,?,?,?,?,?,?)",
        (run_id, "demo", run_id, 1.0, 1.0, 5, "completed", run_id),
    )
    conn.commit()
    conn.close()


def test_scalars_in_sibling_zarr_are_previewed(tmp_path):
    """A study run whose runs.db row carries no scalar history but whose scalars
    live in the sibling ``<study>/runs.<run_id>.zarr`` previews from the zarr,
    not the empty SQLite. Regression: the preview trusted the one recorded store
    path and reported 'emitted no scalar observables' for XArray/zarr runs."""
    import pytest

    pytest.importorskip("xarray")
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    run_id = "demo-baseline"
    _runs_meta_only_db(study_dir / "runs.db", run_id)          # resolves first, empty
    _make_fake_zarr(study_dir / f"runs.{run_id}.zarr")         # the real scalars

    payload, status = build_study_results(tmp_path, "demo")
    assert status == 200
    assert payload["present"] is True
    by_path = {s["path"]: s for s in payload["stores"]}
    assert "enrichment_factor" in by_path, \
        f"zarr scalar should be previewed; got {sorted(by_path)}"
    row = by_path["enrichment_factor"]
    assert row["first"] == 100.0 and row["last"] == 104.0
    assert isinstance(row["sparkline"], list) and len(row["sparkline"]) > 0


def test_study_yaml_run_with_wrong_store_path_falls_back_to_zarr(tmp_path):
    """The viva-genesis ``og-spatial-concentration`` shape: a study.yaml run
    records a (study-relative, SQLite) ``store_path`` that does not resolve /
    holds no scalars, while the scalars were emitted to the sibling
    ``<study>/runs.<run_id>.zarr``. The preview must still surface them."""
    import pytest
    import yaml

    pytest.importorskip("xarray")
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    run_id = "viva.composites.c.c__1__ab"
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({
            "name": "demo",
            "runs": [{
                "name": run_id,
                "status": "completed",
                # points at a SQLite that isn't where the scalars are (and here
                # does not exist at all) — the recorded-but-wrong store.
                "store_path": f"canonical-runs/{run_id}/run.db",
            }],
        }),
        encoding="utf-8",
    )
    _make_fake_zarr(study_dir / f"runs.{run_id}.zarr", leaf="accumulation_peclet")

    payload, status = build_study_results(tmp_path, "demo")
    assert status == 200
    assert payload["present"] is True
    by_path = {s["path"]: s for s in payload["stores"]}
    assert "accumulation_peclet" in by_path, \
        f"zarr scalar should be previewed; got {sorted(by_path)}"


def test_genuinely_scalarless_run_still_previews_empty(tmp_path):
    """Honest bar: a run with no scalar leaves in ANY resolvable store still
    returns ``present: True, stores: []`` — no fabricated sparklines."""
    import pytest

    pytest.importorskip("xarray")
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    run_id = "demo-baseline"
    _runs_meta_only_db(study_dir / "runs.db", run_id)
    # A zarr with only a vector leaf (no scalar) — nothing to preview.
    import xarray as xr
    import numpy as np
    nodes = {
        "experiment_id=e/variant=0/lineage_seed=0":
            xr.Dataset({"time_gen=1": ("emitstep_gen=1", [0.0, 1.0, 2.0])}),
        "experiment_id=e/variant=0/lineage_seed=0/vec":
            xr.Dataset({"generation=1": (("emitstep_gen=1", "id_vec"),
                                         np.array([[1.0, 2.0]] * 3))},
                       coords={"id_vec": ["a", "b"]}),
    }
    xr.DataTree.from_dict(nodes).to_zarr(str(study_dir / f"runs.{run_id}.zarr"), mode="w")

    payload, status = build_study_results(tmp_path, "demo")
    assert status == 200
    assert payload["present"] is True
    assert payload["stores"] == []

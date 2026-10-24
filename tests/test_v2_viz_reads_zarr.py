"""The v2-Visualization render path reads xarray (zarr) runs, not just SQLite.

XArrayEmitter study runs write a per-run zarr store and NO SQLite history rows,
so ``gather_emitter_outputs`` (which fed ``build_viz_composite``) used to return
empty observables for them — the viz ports came back empty. These tests pin the
two seams that fix it: ``_observables_from_zarr`` (zarr → flat per-observable
trajectories) and ``_resolve_observable``'s direct flat-key match.
"""
from __future__ import annotations

import pytest

from vivarium_workbench.lib.investigations import (
    _observables_from_zarr,
    _resolve_observable,
)


def test_resolve_observable_prefers_direct_flat_key():
    # The zarr reader records a full dotted path as a flat key; the resolver must
    # return it directly instead of trying to walk a nested per-tick "stores".
    observables = {
        "stores.species.Lactobacillus": [0.45, 0.6, 1.0],
        "global_time": [0.0, 1.0, 2.0],
    }
    assert _resolve_observable(observables, "stores.species.Lactobacillus") == [0.45, 0.6, 1.0]


def test_resolve_observable_still_walks_nested_when_no_flat_key():
    # SQLite-shaped per-tick nested dicts keep working (no flat key present).
    observables = {
        "stores": [
            {"species": {"Lactobacillus": 0.45}},
            {"species": {"Lactobacillus": 1.0}},
        ],
    }
    assert _resolve_observable(observables, "stores.species.Lactobacillus") == [0.45, 1.0]


def test_observables_from_zarr_round_trip(tmp_path):
    xr = pytest.importorskip("xarray")
    pytest.importorskip("zarr")
    DataTree = getattr(xr, "DataTree", None)
    if DataTree is None:  # older xarray shipped DataTree in datatree pkg
        DataTree = pytest.importorskip("datatree").DataTree

    exp = "experiment_id=E"
    base = f"{exp}/variant=0/lineage_seed=0"
    # Leaf at the lineage_seed node = the time axis; observable leaves hang below it.
    tree = DataTree.from_dict({
        base: xr.Dataset({"time_gen=1": ("t", [0.0, 1.0, 2.0])}),
        f"{base}/stores/species/Lactobacillus": xr.Dataset({"generation=1": ("t", [0.45, 0.6, 1.0])}),
        f"{base}/stores/species/Gardnerella": xr.Dataset({"generation=1": ("t", [0.08, 0.03, 0.0])}),
    })
    store = tmp_path / "runs.E.zarr"
    tree.to_zarr(str(store))

    obs = _observables_from_zarr(store)
    assert obs.get("stores.species.Lactobacillus") == [0.45, 0.6, 1.0]
    assert obs.get("stores.species.Gardnerella") == [0.08, 0.03, 0.0]
    # the lineage_seed-node leaf becomes the time axis (both aliases)
    assert obs.get("global_time") == [0.0, 1.0, 2.0]
    assert obs.get("time") == [0.0, 1.0, 2.0]


def test_observables_from_zarr_missing_store_is_empty(tmp_path):
    assert _observables_from_zarr(tmp_path / "does-not-exist.zarr") == {}

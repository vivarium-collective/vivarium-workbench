"""Unit tests for vivarium_workbench.server._collect_study_observables.

v2ecoli friction #14 (2026-05-19): the study-run code path was not passing
emit_paths to inject_emitter_for_paths, so every history.state row was
just {"_tick": <global_time>}. _collect_study_observables sweeps the study
spec for every observable-shaped path declaration so the run handler can
wire inject_emitter_for_paths automatically.
"""
from vivarium_workbench.lib.study_spec import (
    collect_study_observables as _collect_study_observables,
)


def test_empty_spec_returns_empty_list():
    assert _collect_study_observables({}) == []
    assert _collect_study_observables({"readouts": [], "behavior_tests": []}) == []


def test_readouts_store_path_extracted():
    """v2ecoli's primary observable declaration site."""
    spec = {
        "readouts": [
            {"name": "free_dnaA",
             "store_path": "agents.0.listeners.dnaA_binding.free_total"},
            {"name": "chromosome_occupied",
             "store_path": "agents.0.listeners.dnaA_binding.chromosome.occupied_fraction"},
        ],
    }
    paths = _collect_study_observables(spec)
    assert paths == [
        "agents/0/listeners/dnaA_binding/free_total",
        "agents/0/listeners/dnaA_binding/chromosome/occupied_fraction",
    ]


def test_behavior_tests_simple_measure_path():
    spec = {
        "behavior_tests": [
            {"name": "monotonic",
             "measure": {"kind": "listener_path",
                         "path": "listeners.dnaA_binding.chromosome.occupied_fraction",
                         "reduce": "series", "window": "full"}},
        ],
    }
    paths = _collect_study_observables(spec)
    assert paths == ["listeners/dnaA_binding/chromosome/occupied_fraction"]


def test_behavior_tests_nested_xy_paths():
    """cross_threshold / xy_correlation shapes carry paths in nested fields."""
    spec = {
        "behavior_tests": [
            {"name": "cross",
             "measure": {
                 "kind": "cross_threshold",
                 "series_x": {"path": "listeners.A"},
                 "series_y": {"path": "listeners.B"},
             }},
            {"name": "correlate",
             "measure": {
                 "kind": "xy_correlation",
                 "x": {"path": "listeners.C"},
                 "y": {"path": "listeners.D"},
             }},
            {"name": "time_lag",
             "measure": {
                 "kind": "time_lag_between",
                 "series_a": {"path": "listeners.E"},
                 "series_b": {"path": "listeners.F"},
             }},
        ],
    }
    paths = _collect_study_observables(spec)
    assert paths == [
        "listeners/A", "listeners/B",
        "listeners/C", "listeners/D",
        "listeners/E", "listeners/F",
    ]


def test_simulation_set_observe_list():
    spec = {
        "simulation_set": [
            {"name": "sim-a", "observe": ["stores/level", "stores/flux"]},
            {"name": "sim-b", "observe": "stores/extra"},
        ],
    }
    paths = _collect_study_observables(spec)
    assert paths == ["stores/level", "stores/flux", "stores/extra"]


def test_deduplication_preserves_first_occurrence():
    """Real study yamls cite the same path from a readout AND a behavior_test;
    the emitter only needs one wire per leaf."""
    spec = {
        "readouts": [
            {"store_path": "listeners.dnaA_binding.free_total"},
        ],
        "behavior_tests": [
            {"measure": {"path": "listeners.dnaA_binding.free_total"}},
        ],
    }
    paths = _collect_study_observables(spec)
    assert paths == ["listeners/dnaA_binding/free_total"]


def test_expression_valued_path_is_rejected_not_garbled():
    """Regression (item 80/83, 2026-08-21): a real 'showcase' study's
    mass-fraction behavior_test declared its measure as `kind: path` but
    stored a whole division expression as the value —
    "listeners.mass.protein_mass / listeners.mass.dry_mass" — instead of a
    bare address (the sibling doubling-time-in-band measure correctly uses
    `formula` for its own computed value). Blindly slash-joining this
    silently produced a garbled "observable" that corrupted a real dispatch's
    recorded engine_process_reports. Any segment containing whitespace or an
    operator marks the whole value as not a real path — skip it entirely,
    the same tolerant-skip behavior already used for other malformed input,
    rather than pass through nonsense."""
    spec = {
        "behavior_tests": [
            {"name": "mass-fraction-physiological",
             "measure": {"kind": "path",
                         "path": "listeners.mass.protein_mass / listeners.mass.dry_mass"}},
            {"name": "doubling-time-in-band",
             "measure": {"kind": "derived",
                         "formula": "0.011552453009332421 / listeners.mass.instantaneous_growth_rate"}},
            {"name": "real-one",
             "measure": {"kind": "path", "path": "listeners.mass.real_leaf"}},
        ],
    }
    paths = _collect_study_observables(spec)
    # The malformed `path` value is skipped; `formula` was never read (by
    # design — this function only recognises `path`/`store_path`/etc., never
    # `formula`); the real, well-formed path is still collected.
    assert paths == ["listeners/mass/real_leaf"]


def test_dot_and_slash_separators_both_accepted():
    spec = {
        "readouts": [
            {"store_path": "agents.0.foo"},
            {"store_path": "agents/0/bar"},
        ],
    }
    paths = _collect_study_observables(spec)
    assert paths == ["agents/0/foo", "agents/0/bar"]


def test_malformed_entries_are_skipped():
    """A study mid-edit may have unparsed fields; the sweep must be tolerant."""
    spec = {
        "readouts": [
            None,
            "just-a-string",
            {"no_store_path_field": "..."},
            {"store_path": ""},
            {"store_path": "agents.0.real_one"},
        ],
        "behavior_tests": [
            {},
            {"measure": None},
            {"measure": "string-not-dict"},
            {"measure": {"path": None}},
            {"measure": {"path": "listeners.real_one"}},
        ],
    }
    paths = _collect_study_observables(spec)
    assert paths == ["agents/0/real_one", "listeners/real_one"]


# ---------------------------------------------------------------------------
# C-state-3c: the lib copy (study_spec.collect_study_observables) must recognise
# every declaration shape, including the v4 comparative_visualizations[] loop.
# ---------------------------------------------------------------------------


def test_collects_comparative_visualizations():
    """Regression: comparative_visualizations[] (v4) contributes observables."""
    spec = {"comparative_visualizations": [{"observable_path": "agents.0.cv_obs"}]}
    assert _collect_study_observables(spec) == ["agents/0/cv_obs"]

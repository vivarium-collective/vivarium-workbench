"""Unit tests for vivarium_dashboard.server._collect_study_observables.

v2ecoli friction #14 (2026-05-19): the study-run code path was not passing
emit_paths to inject_emitter_for_paths, so every history.state row was
just {"_tick": <global_time>}. _collect_study_observables sweeps the study
spec for every observable-shaped path declaration so the run handler can
wire inject_emitter_for_paths automatically.
"""
from vivarium_dashboard.server import _collect_study_observables


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

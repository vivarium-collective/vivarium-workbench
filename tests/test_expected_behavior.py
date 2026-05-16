"""Tests for the lifted expected_behavior DSL evaluator and v4 schema validator.

Covers:
- evaluate() with all existing measure kinds and expect operators.
- The three new measure primitives: event_count, pre_post_event, concentration.
- _validate_expected_behavior via load_spec() with v4 structured entries.
- Backward compatibility with free-form string lists.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

import pytest
import yaml

from vivarium_dashboard.lib.expected_behavior import (
    EvaluationResult,
    MissingExpectError,
    MissingMeasureError,
    bulk_count,
    concentration,
    evaluate,
    event_count,
    listener_value,
    pre_post_event,
    window,
)
from vivarium_dashboard.lib.investigations import InvestigationSpecError, load_spec


# ─── Synthetic state factories ──────────────────────────────────────────────


DNAA_ID = "MONOMER0-160[c]"
MRNA_ID = "EG10235_RNA"


def _make_state(
    dnaA_count: int = 500,
    *,
    n_bound: int = 0,
    n_init: int = 0,
    cell_volume: float = 1.0,
    initiation_events: int = 0,
) -> dict:
    return {
        "agents": {
            "0": {
                "bulk": {
                    "id": [DNAA_ID, MRNA_ID],
                    "count": [dnaA_count, 12],
                },
                "listeners": {
                    "rna_synth_prob": {"n_actual_bound": [n_bound]},
                    "rnap_data": {"rna_init_event": [n_init]},
                    "mass": {"cell_volume": cell_volume},
                    "replication": {"initiation_events": initiation_events},
                },
            }
        }
    }


def _snap(step: int, dnaA_count: int = 500, **kwargs) -> dict:
    return {"step": step, "time": float(step * 60), "state": _make_state(dnaA_count, **kwargs)}


# ─── State accessor unit tests ───────────────────────────────────────────────


def test_bulk_count_found():
    state = _make_state(450)
    assert bulk_count(state, DNAA_ID) == 450


def test_bulk_count_not_found():
    state = _make_state(450)
    assert bulk_count(state, "UNKNOWN[c]") is None


def test_bulk_count_tuple_format():
    state = {"agents": {"0": {"bulk": [(DNAA_ID, 300), (MRNA_ID, 5)]}}}
    assert bulk_count(state, DNAA_ID) == 300


def test_listener_value_found():
    state = _make_state(500, n_bound=80)
    v = listener_value(state, "listeners.rna_synth_prob.n_actual_bound")
    assert v == [80]


def test_listener_value_missing_segment():
    state = _make_state()
    assert listener_value(state, "listeners.does_not_exist.foo") is None


# ─── Window selection ────────────────────────────────────────────────────────


def test_window_full():
    h = [_snap(i) for i in range(10)]
    assert window(h, "full") == h


def test_window_second_half():
    h = [_snap(i) for i in range(10)]
    result = window(h, "second_half")
    assert result == h[5:]


def test_window_post_initiation_stub():
    h = [_snap(i) for i in range(10)]
    assert window(h, "post_initiation_10min") == []


def test_window_unknown_raises():
    with pytest.raises(ValueError, match="unknown window"):
        window([], "nonexistent")


# ─── New primitive: event_count ──────────────────────────────────────────────


def test_event_count_zero():
    h = [_snap(i, initiation_events=0) for i in range(5)]
    pred = {"observable": "listeners.replication.initiation_events", "op": ">", "value": 0}
    assert event_count(h, pred) == 0


def test_event_count_nonzero():
    h = [_snap(i, initiation_events=(1 if i == 3 else 0)) for i in range(6)]
    pred = {"observable": "listeners.replication.initiation_events", "op": ">", "value": 0}
    assert event_count(h, pred) == 1


def test_event_count_via_evaluate():
    h = [_snap(i, initiation_events=(1 if i == 3 else 0)) for i in range(6)]
    entry = {
        "name": "initiation-count",
        "en": "Exactly one initiation event occurs.",
        "given": {"run": "baseline", "window": "full"},
        "measure": {
            "kind": "event_count",
            "predicate": {
                "observable": "listeners.replication.initiation_events",
                "op": ">",
                "value": 0,
            },
        },
        "expect": {"op": "in_range", "low": 1, "high": 1},
    }
    result = evaluate(entry, h)
    assert result.passed, result.message


# ─── New primitive: concentration ────────────────────────────────────────────


def test_concentration_basic():
    state = _make_state(dnaA_count=500, cell_volume=2.0)
    c = concentration(state, DNAA_ID, "listeners.mass.cell_volume")
    assert c == 250.0


def test_concentration_missing_molecule():
    state = _make_state(dnaA_count=500, cell_volume=2.0)
    c = concentration(state, "UNKNOWN[c]", "listeners.mass.cell_volume")
    assert c is None


def test_concentration_missing_volume():
    state = _make_state(dnaA_count=500, cell_volume=0.0)
    # volume=0 → falsy → returns None
    c = concentration(state, DNAA_ID, "listeners.mass.cell_volume")
    assert c is None


def test_concentration_via_evaluate():
    h = [_snap(i, dnaA_count=500, cell_volume=2.0) for i in range(20)]
    entry = {
        "name": "dnaa-concentration-range",
        "en": "DnaA concentration (count/volume) is between 100 and 400.",
        "given": {"run": "baseline", "window": "full"},
        "measure": {
            "kind": "concentration",
            "molecule": DNAA_ID,
            "volume_path": "listeners.mass.cell_volume",
            "reduce": "median",
        },
        "expect": {"op": "in_range", "low": 100.0, "high": 400.0},
    }
    result = evaluate(entry, h)
    assert result.passed, result.message


# ─── New primitive: pre_post_event ───────────────────────────────────────────


def test_pre_post_event_found():
    # Event at step 5
    h = [_snap(i, n_init=2, initiation_events=(1 if i == 5 else 0)) for i in range(10)]
    pred = {"observable": "listeners.replication.initiation_events", "op": ">", "value": 0}
    result = pre_post_event(h, pred, before_min=5.0, after_min=5.0)
    assert result is not None
    pre, post = result
    assert len(pre) > 0
    assert len(post) > 0


def test_pre_post_event_not_found():
    h = [_snap(i, initiation_events=0) for i in range(10)]
    pred = {"observable": "listeners.replication.initiation_events", "op": ">", "value": 0}
    assert pre_post_event(h, pred, before_min=5.0, after_min=5.0) is None


def test_pre_post_event_ratio_via_evaluate():
    """Simulate: after event, n_init rises 2x. Assert ratio_at_least 1.5."""
    pre_snaps = [_snap(i, n_init=5, initiation_events=0) for i in range(5)]
    event_snap = _snap(5, n_init=5, initiation_events=1)
    post_snaps = [_snap(6 + i, n_init=10, initiation_events=0) for i in range(5)]
    h = pre_snaps + [event_snap] + post_snaps

    entry = {
        "name": "post-initiation-gene-dosage-spike",
        "en": "After initiation, dnaA transcription rate rises by at least 50%.",
        "given": {"run": "baseline", "window": "full"},
        "measure": {
            "kind": "listener_sum",
            "path": "listeners.rnap_data.rna_init_event",
            "reduce": "pre_post_event_ratio",
            "event_predicate": {
                "observable": "listeners.replication.initiation_events",
                "op": ">",
                "value": 0,
            },
            "before_min": 5.0,
            "after_min": 5.0,
        },
        "expect": {"op": "ratio_at_least", "ratio": 1.5},
    }
    result = evaluate(entry, h)
    assert result.passed, result.message


# ─── Existing measure kinds and expect operators ─────────────────────────────


@pytest.fixture
def steady_history():
    """40 snapshots with DnaA hovering at ~520 ± small noise (CV < 0.05)."""
    rng = random.Random(42)
    return [_snap(i, dnaA_count=int(rng.gauss(520, 20))) for i in range(40)]


@pytest.fixture
def decay_history():
    """Exponential decay from 520 (half-life 40 steps)."""
    return [
        _snap(i, dnaA_count=int(520 * math.exp(-i * math.log(2) / 40)))
        for i in range(60)
    ]


def test_bulk_count_in_range(steady_history):
    entry = {
        "name": "count-in-range",
        "en": "DnaA count is between 300 and 800.",
        "given": {"run": "baseline", "window": "second_half"},
        "measure": {"kind": "bulk_count", "id": DNAA_ID, "reduce": "median"},
        "expect": {"op": "in_range", "low": 300, "high": 800},
    }
    result = evaluate(entry, steady_history)
    assert result.passed, result.message


def test_rolling_cv_below(steady_history):
    entry = {
        "name": "cv-below",
        "en": "DnaA CV is below 0.10.",
        "given": {"run": "baseline", "window": "full"},
        "measure": {"kind": "bulk_count", "id": DNAA_ID, "reduce": "series"},
        "expect": {"op": "rolling_cv_below", "window_steps": 5, "threshold": 0.10},
    }
    result = evaluate(entry, steady_history)
    assert result.passed, result.message


def test_ratio_at_most_decay(decay_history):
    entry = {
        "name": "decay-ratio",
        "en": "DnaA drops by ≥30% (last/first ≤ 0.70).",
        "given": {"run": "variant", "window": "full"},
        "measure": {"kind": "bulk_count", "id": DNAA_ID, "reduce": "first_and_last"},
        "expect": {"op": "ratio_at_most", "ratio": 0.70},
    }
    result = evaluate(entry, decay_history)
    assert result.passed, result.message


def test_monotonic_decreasing(decay_history):
    entry = {
        "name": "monotonic-decay",
        "en": "DnaA count decreases monotonically.",
        "given": {"run": "variant", "window": "full"},
        "measure": {"kind": "bulk_count", "id": DNAA_ID, "reduce": "series"},
        "expect": {"op": "monotonic_decreasing", "allow_rebound_pct": 5},
    }
    result = evaluate(entry, decay_history)
    assert result.passed, result.message


def test_pearson_below():
    """Inverse correlation between n_bound and n_init should give r < -0.3."""
    rng = random.Random(0)
    h = []
    for step in range(40):
        n_bound = 80 + (step % 10)
        n_init = 12 - (n_bound - 80) // 2
        s = {"step": step, "time": float(step * 60),
             "state": _make_state(500, n_bound=n_bound, n_init=n_init)}
        h.append(s)
    entry = {
        "name": "pearson-test",
        "en": "DnaA bound inversely correlated with init events.",
        "given": {"run": "baseline", "window": "second_half"},
        "measure": {
            "kind": "xy_correlation",
            "x": {"kind": "listener_sum", "path": "listeners.rna_synth_prob.n_actual_bound"},
            "y": {"kind": "listener_sum", "path": "listeners.rnap_data.rna_init_event"},
        },
        "expect": {"op": "pearson_below", "threshold": -0.3},
    }
    result = evaluate(entry, h)
    assert result.passed, result.message


# ─── EvaluationResult backward compat ────────────────────────────────────────


def test_evaluation_result_unpacks_as_tuple():
    h = [_snap(i) for i in range(10)]
    entry = {
        "name": "test",
        "en": "test",
        "given": {},
        "measure": {"kind": "bulk_count", "id": DNAA_ID, "reduce": "median"},
        "expect": {"op": "in_range", "low": 0, "high": 10000},
    }
    passed, message = evaluate(entry, h)
    assert passed is True


# ─── Empty / missing history edge cases ─────────────────────────────────────


def test_empty_history_returns_failure():
    entry = {
        "name": "x",
        "en": "x",
        "given": {"window": "full"},
        "measure": {"kind": "bulk_count", "id": DNAA_ID, "reduce": "median"},
        "expect": {"op": "in_range", "low": 0, "high": 1000},
    }
    # Empty history → window returns [] → EvaluationResult(passed=False)
    result = evaluate(entry, [])
    assert not result.passed


def test_post_initiation_window_stub_returns_failure():
    h = [_snap(i) for i in range(10)]
    entry = {
        "name": "stub-test",
        "en": "stub",
        "given": {"window": "post_initiation_10min"},
        "measure": {"kind": "bulk_count", "id": DNAA_ID, "reduce": "median"},
        "expect": {"op": "in_range", "low": 0, "high": 1000},
    }
    result = evaluate(entry, h)
    assert not result.passed
    assert "empty" in result.message


# ─── v4 schema validator: expected_behavior ──────────────────────────────────


_BASE_SPEC = {
    "schema_version": 4,
    "name": "test-study",
    "baseline": [{"name": "b", "composite": "pkg.c", "params": {}}],
    "variants": [],
    "interventions": [],
    "runs": [],
    "visualizations": [],
    "conclusion": "",
    "objective": "",
    "parent_studies": [],
    "tests": {
        "auto_discover": True,
        "data_source": "latest_run",
        "pytest_args": [],
        "last_results": None,
    },
    "references": [],
    "implementation_tasks": "",
}


def test_v4_freeform_strings_pass(tmp_path):
    spec = dict(_BASE_SPEC)
    spec["expected_behavior"] = [
        "DnaA count stays between 300 and 800.",
        "Autorepression keeps concentration stable.",
    ]
    p = tmp_path / "study.yaml"
    p.write_text(yaml.safe_dump(spec))
    loaded = load_spec(p)
    assert loaded["expected_behavior"][0].startswith("DnaA")


def test_v4_structured_entries_pass(tmp_path):
    spec = dict(_BASE_SPEC)
    spec["expected_behavior"] = [
        {
            "name": "dnaa-count-in-range",
            "en": "DnaA monomer count is between 300 and 800.",
            "given": {"run": "baseline", "window": "second_half"},
            "measure": {"kind": "bulk_count", "id": "MONOMER0-160[c]", "reduce": "median"},
            "expect": {"op": "in_range", "low": 300, "high": 800},
            "status": "implemented",
        }
    ]
    p = tmp_path / "study.yaml"
    p.write_text(yaml.safe_dump(spec))
    loaded = load_spec(p)
    assert loaded["expected_behavior"][0]["name"] == "dnaa-count-in-range"


def test_v4_structured_missing_name_raises(tmp_path):
    spec = dict(_BASE_SPEC)
    spec["expected_behavior"] = [
        {
            "en": "Missing name field.",
            "measure": {"kind": "bulk_count", "id": "X"},
            "expect": {"op": "in_range", "low": 0, "high": 1},
        }
    ]
    p = tmp_path / "study.yaml"
    p.write_text(yaml.safe_dump(spec))
    with pytest.raises(InvestigationSpecError, match="name is required"):
        load_spec(p)


def test_v4_structured_missing_measure_raises(tmp_path):
    spec = dict(_BASE_SPEC)
    spec["expected_behavior"] = [
        {
            "name": "no-measure",
            "en": "Missing measure.",
            "expect": {"op": "in_range", "low": 0, "high": 1},
        }
    ]
    p = tmp_path / "study.yaml"
    p.write_text(yaml.safe_dump(spec))
    with pytest.raises(InvestigationSpecError, match="measure is required"):
        load_spec(p)


def test_v4_structured_missing_expect_raises(tmp_path):
    spec = dict(_BASE_SPEC)
    spec["expected_behavior"] = [
        {
            "name": "no-expect",
            "en": "Missing expect.",
            "measure": {"kind": "bulk_count", "id": "X"},
        }
    ]
    p = tmp_path / "study.yaml"
    p.write_text(yaml.safe_dump(spec))
    with pytest.raises(InvestigationSpecError, match="expect is required"):
        load_spec(p)


def test_v4_unknown_measure_op_allowed(tmp_path):
    """Unknown measure.kind / expect.op are NOT validated at schema time."""
    spec = dict(_BASE_SPEC)
    spec["expected_behavior"] = [
        {
            "name": "future-primitive",
            "en": "Uses a future measure kind.",
            "measure": {"kind": "future_primitive_not_yet_invented", "id": "X"},
            "expect": {"op": "future_op"},
        }
    ]
    p = tmp_path / "study.yaml"
    p.write_text(yaml.safe_dump(spec))
    loaded = load_spec(p)
    assert loaded["expected_behavior"][0]["measure"]["kind"] == "future_primitive_not_yet_invented"

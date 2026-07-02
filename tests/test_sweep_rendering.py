"""Pass B sweep-table rendering tests.

Asserts that simulation_set entries with `kind: sweep` (the Pass B
structured sweep shape) flow through the study-detail.html template
without raising, render the axes / runs / candidates / rejection
reasons, and round-trip alongside back-compat single entries.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib.study_page import (
    render_study_detail_html as _lib_render_study_detail_html,
)

# These are pure template-render tests: the spec dict is supplied directly and
# the workspace filesystem is not read meaningfully (all ws_root reads in the
# lib fn are tolerant of an empty/missing workspace). Provide a throwaway
# workspace root so the 2-arg call-sites below stay unchanged.
_WS = Path(tempfile.mkdtemp(prefix="sweep_render_ws_"))


def _render_study_detail_html(name: str, spec: dict) -> str:
    return _lib_render_study_detail_html(_WS, name, spec)


SINGLE_ENTRY = {
    "name": "baseline",
    "base_model": "pkg.composites.x",
    "duration_min": 60,
    "seeds": [0, 1, 2],
    "readouts": ["dnaA_count"],
    "applies_tests": ["dnaa-steady"],
}


SWEEP_ENTRY = {
    "name": "te-multiplier-sweep",
    "kind": "sweep",
    "base_model": "pkg.composites.dnaa",
    "axes": [
        {"parameter": "te_multiplier", "values": [1.0, 5.0, 10.0, 25.0]},
        {"parameter": "rnap_count", "values": [100, 200]},
    ],
    "seeds": [0, 1, 2],
    "duration": 3600,
    "metrics": ["dnaA_steady_count", "autorepression_signal"],
    "pass_fail_tests": ["autorepression-correlation"],
    "candidate_selection": "Top-2 by pass-rate; tiebreak by min |observed - expected|.",
    "runs": [
        {
            "axis_values": {"te_multiplier": 10.0, "rnap_count": 200},
            "seed": 1,
            "run_id": "run-abc",
            "metrics": {"dnaA_steady_count": 412, "autorepression_signal": 0.81},
            "test_results": {"autorepression-correlation": "pass"},
        },
        {
            "axis_values": {"te_multiplier": 1.0, "rnap_count": 100},
            "seed": 0,
            "run_id": "run-def",
            "metrics": {"dnaA_steady_count": 12},
            "test_results": {"autorepression-correlation": "fail"},
        },
    ],
    "aggregate_metrics": {"pass_rate": 0.5, "candidate_count": 1},
    "candidates_selected": ["run-abc"],
    "rejection_reasons": {"run-def": "below dnaA_steady_count threshold (12 < 350)"},
    "artifact_path": "out/sweeps/te-multiplier-sweep.csv",
    "status": "ran",
}


def _spec(simulation_set: list) -> dict:
    """Minimal study spec the template can render."""
    return {
        "name": "test-study",
        "baseline": [{"name": "b1", "composite": "pkg.composites.x"}],
        "simulation_set": simulation_set,
    }


# ---------------------------------------------------------------------------
# Template render — does not raise + key fields appear in HTML
# ---------------------------------------------------------------------------


def test_sweep_entry_renders_without_raising():
    html = _render_study_detail_html("test-study", _spec([SWEEP_ENTRY]))
    assert html  # non-empty
    assert "te-multiplier-sweep" in html
    assert "SWEEP" in html  # the badge label
    assert "Axes" in html


def test_sweep_axes_render_parameters_and_values():
    html = _render_study_detail_html("test-study", _spec([SWEEP_ENTRY]))
    assert "te_multiplier" in html
    assert "rnap_count" in html
    # Each axis value appears as a code chip.
    for v in (1.0, 5.0, 10.0, 25.0):
        assert str(v) in html
    for v in (100, 200):
        assert str(v) in html


def test_sweep_runs_table_renders_run_ids_and_test_results():
    html = _render_study_detail_html("test-study", _spec([SWEEP_ENTRY]))
    assert "run-abc" in html
    assert "run-def" in html
    # Test result badges.
    assert "autorepression-correlation: pass" in html
    assert "autorepression-correlation: fail" in html


def test_sweep_aggregate_metrics_render():
    html = _render_study_detail_html("test-study", _spec([SWEEP_ENTRY]))
    assert "pass_rate=0.5" in html
    assert "candidate_count=1" in html


def test_sweep_candidates_selected_highlighted():
    html = _render_study_detail_html("test-study", _spec([SWEEP_ENTRY]))
    assert "Candidates selected" in html
    assert "run-abc" in html
    assert "candidate" in html  # the "★ candidate" inline marker


def test_sweep_rejection_reasons_render_collapsible():
    html = _render_study_detail_html("test-study", _spec([SWEEP_ENTRY]))
    assert "Rejection reasons" in html
    assert "run-def" in html
    assert "below dnaA_steady_count threshold" in html


def test_sweep_with_no_runs_yet_renders_placeholder():
    """An axes-only sweep (kind: sweep, no runs[] populated) still renders."""
    sweep = {
        "name": "minimal-sweep",
        "kind": "sweep",
        "axes": [{"parameter": "x", "values": [1, 2, 3]}],
    }
    html = _render_study_detail_html("test-study", _spec([sweep]))
    assert "minimal-sweep" in html
    assert "No runs executed yet" in html


# ---------------------------------------------------------------------------
# Back-compat: single entries still render
# ---------------------------------------------------------------------------


def test_single_entry_still_renders():
    html = _render_study_detail_html("test-study", _spec([SINGLE_ENTRY]))
    assert "baseline" in html
    assert "pkg.composites.x" in html
    # NOT the sweep badge.
    assert "SWEEP" not in html
    # NOT a sweep axes block.
    assert "Axes (" not in html


def test_mixed_single_and_sweep_entries_render():
    html = _render_study_detail_html(
        "test-study",
        _spec([SINGLE_ENTRY, SWEEP_ENTRY]),
    )
    # Both entry names appear.
    assert "baseline" in html
    assert "te-multiplier-sweep" in html
    # Sweep badge appears (once).
    assert "SWEEP" in html


def test_empty_simulation_set_renders_no_section():
    html = _render_study_detail_html("test-study", _spec([]))
    # Section is hidden when simulation_set is empty/falsy.
    assert "Simulation set" not in html


# ---------------------------------------------------------------------------
# Endpoint round-trip: simulation_set with sweep entries flows through
# _study_detail_spec without mutation.
# ---------------------------------------------------------------------------


def test_study_yaml_sweep_entry_roundtrips_through_yaml(tmp_path):
    """A study.yaml with a sweep entry parses back into the same dict."""
    spec_path = tmp_path / "study.yaml"
    spec_path.write_text(yaml.safe_dump(_spec([SWEEP_ENTRY]), sort_keys=False))
    loaded = yaml.safe_load(spec_path.read_text())
    sweep = loaded["simulation_set"][0]
    assert sweep["kind"] == "sweep"
    assert sweep["axes"][0]["parameter"] == "te_multiplier"
    assert sweep["candidates_selected"] == ["run-abc"]
    assert sweep["rejection_reasons"]["run-def"].startswith("below dnaA_steady_count")
    # Render the loaded copy — verifies we have not corrupted the structure.
    html = _render_study_detail_html("test-study", loaded)
    assert "te-multiplier-sweep" in html
    assert "Rejection reasons" in html

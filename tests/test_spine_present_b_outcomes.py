"""Thread-B / Task 1 (B3): per-test computed outcomes are surfaced, connected
to their run + band, and visually separated from the authored outcome.

The JS rendering has no browser harness, so these are STRUCTURAL tests: they
assert the renderers reference the persisted computed data (measured_value,
evaluated_by, reconcile) and emit the markup that distinguishes code-computed
from authored (separate chips) + the reconcile:divergent badge + a run link +
the pass_if band — following the param-enforcement-banner pattern. The report
must NOT dump the merged authored+computed blob as raw k:v.
"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).parent.parent / "vivarium_workbench"


def test_study_detail_renders_per_test_computed_outcomes():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    # Per-test (not aggregate-only): a dedicated row renderer keyed off the
    # computed outcome fields.
    assert "_renderComputedOutcomeRow" in js
    assert "computed-outcome-row" in js
    assert "measured_value" in js
    assert "evaluated_by" in js
    assert "reconcile" in js
    # Code-computed vs authored kept in SEPARATE labeled chips.
    assert "outcome-chip-computed" in js
    assert "outcome-chip-authored" in js
    assert "code computed" in js
    # Prominent divergence badge + connection to the run + the band.
    assert "reconcile-divergent" in js
    assert "#run-" in js
    assert "pass_if" in js


def test_runs_table_has_test_results_column():
    html = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")
    assert "Test results" in html
    assert "run-test-results" in html
    # Sourced from each run's computed_outcomes; rows anchorable by run id.
    assert "computed_outcomes" in html
    assert 'id="run-{{ r.run_id or r.name }}"' in html


def test_report_separates_authored_from_computed_no_raw_dump():
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    # The report renders measured_value as a styled row, authored vs computed
    # in separate chips, divergence badged, linked to its run + band.
    assert "outcome-chip-computed" in js
    assert "outcome-chip-authored" in js
    assert "reconcile-divergent" in js
    assert "computed-outcome-row" in js
    # The old merged raw k:v dump is gone (it blended authored + computed).
    assert "Object.keys(out).filter(function(k){return k !== 'result';})" not in js

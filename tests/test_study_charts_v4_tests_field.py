"""Regression test for render_v4_test_charts' reading of the tests field.

In schema_version 4 study specs, the top-level ``tests:`` key holds the
pytest-autodiscover *config dict* (``auto_discover``/``data_source``/…), while
the behavior-test *list* lives under ``behavior_tests:``. The chart renderer
used to iterate ``spec["tests"]`` unconditionally, so on a v4 spec it walked
the config dict's string keys and crashed with
``'str' object has no attribute 'get'`` — 500-ing the whole study-charts
payload (including static charts). It must instead read the behavior-test list
and degrade to an empty list when no per-test measure path is resolvable.
"""
from __future__ import annotations

from vivarium_workbench.lib.study_charts import render_v4_test_charts


def test_v4_tests_config_dict_does_not_crash():
    """A v4 spec whose `tests` is the autodiscover config dict must not raise."""
    spec = {
        "schema_version": 4,
        # This is the shape that crashed: `tests` is a dict, not a list.
        "tests": {"auto_discover": True, "data_source": "latest_run",
                  "pytest_args": [], "last_results": None},
        "behavior_tests": [
            {"name": "some-test", "classification": "primary",
             "measure": {"kind": "live_cell_count", "cells_path": "cells"},
             "pass_if": {"op": "at_least", "threshold": 4}},
        ],
    }
    # No runs.db and no measure.path on the behavior tests → returns [] cleanly.
    assert render_v4_test_charts(spec, None, fallback_db=None) == []


def test_v4_list_tests_still_read():
    """Back-compat: a proper list-of-dicts under `tests` is still honored."""
    spec = {
        "schema_version": 4,
        "tests": [
            {"name": "t", "measure": {"path": "cells/x", "index": None},
             "pass_if": {}},
        ],
    }
    # No db to read from, so still [] — but crucially it iterated the list
    # without falling back and without raising.
    assert render_v4_test_charts(spec, None, fallback_db=None) == []


# --- Honesty fix: unresolvable measure paths get a visible stub, not a ---
# --- silent drop (see study_charts.py ~:630/643) --------------------------

def test_unresolvable_measure_path_yields_refusal(tmp_path):
    """A declared test whose path never resolves (in either source) must
    still produce a card — a reviewer should see it flagged, not miss it.
    Since the P1 typed run→finding work, that card is a reason-bearing
    ``Refusal`` (``status == "refused"``) naming the missing path, not a
    silent drop."""
    runs_db = tmp_path / "runs.db"
    runs_db.touch()  # a real (if schema-less) db file → reaches the per-test loop
    spec = {
        "schema_version": 4,
        "tests": [
            {"name": "ghost-test", "classification": "primary",
             "measure": {"path": "no.such.observable"}, "pass_if": {}},
        ],
    }
    charts = render_v4_test_charts(spec, runs_db, fallback_db=None)
    assert len(charts) == 1
    stub = charts[0]
    assert stub["status"] == "refused"
    assert stub["key"] == "v4-ghost-test"
    assert stub["missing"] == ["no.such.observable"]
    assert "no.such.observable" in stub["reason"]
    assert "svg" not in stub


def test_non_chart_tests_skipped_from_gallery_data_gap_kept(tmp_path):
    """Tests with no plottable series are not visualizations: a test with no
    `measure.path` (a scalar assertion) and a `kind: derived` scalar readout are
    graded with their verdict on the Audit tab, so they are skipped from the
    Visualizations gallery rather than emitted as empty stubs. A non-derived path
    with no image IS a genuine data gap and is kept visible."""
    runs_db = tmp_path / "runs.db"
    runs_db.touch()
    spec = {
        "schema_version": 4,
        "tests": [
            {"name": "no-path-test", "classification": "primary",
             "measure": {"kind": "live_cell_count"}, "pass_if": {}},
            {"name": "derived-scalar-test", "classification": "primary",
             "measure": {"path": "growth_rate", "kind": "derived"}, "pass_if": {}},
            # A non-derived path with no image is a real data gap -> kept.
            {"name": "unresolvable-test", "classification": "primary",
             "measure": {"path": "still.unresolvable"}, "pass_if": {}},
        ],
    }
    charts = render_v4_test_charts(spec, runs_db, fallback_db=None)
    keys = {c["key"] for c in charts}
    assert "v4-no-path-test" not in keys          # scalar assertion -> Audit only
    assert "v4-derived-scalar-test" not in keys   # scalar readout -> Audit only
    assert "v4-unresolvable-test" in keys         # genuine data gap -> kept
    assert len(charts) == 1

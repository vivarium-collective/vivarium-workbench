"""P1 of the typed run→finding chain design: reason-bearing refusals instead
of silent measure/figure failures.

Two boundaries, both additive/non-breaking:

  - Boundary G (``wb.measure.read``, ``lib/study_charts.py``): a behavior
    test's ``measure.path`` with no image ANYWHERE in the run's store used to
    silently drop the chart (``if not xs: continue``). It must instead
    produce a reason-bearing refusal naming the missing path and the leaves
    the store DOES carry.
  - Boundary V (``wb.figure.render``, ``lib/refresh_viz.py``): an
    address-only visualization entry (no ``render``/``chart``) used to
    always emit a bare ``needs_manual_refresh``. A NEW optional
    ``requires: [observable]`` field lets it refuse with a reason when a
    required observable is absent from the run's store — but an entry with
    no ``requires`` must keep the EXACT prior behavior (baked/self-contained
    figures still work unchanged).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from vivarium_workbench.lib.refresh_viz import refresh_study_viz
from vivarium_workbench.lib.study_charts import render_v4_test_charts


def _make_runs_db(db_path: Path, state: dict, run_id: str = "run-1") -> None:
    """Write a minimal process_bigraph SQLiteEmitter-shaped runs.db with one
    run, one history row (mirrors tests/test_explorer_data.py's fixture
    shape — simulations + history tables, one full-state JSON blob)."""
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
        (run_id, "baseline", "2026-01-01T00:00:00", "2026-01-01T00:01:00", 60.0),
    )
    conn.execute(
        "INSERT INTO history VALUES (?,?,?,?)",
        (run_id, 0, 0.0, json.dumps(state)),
    )
    conn.commit()
    conn.close()


_STATE = {"recruitment_index": 0.7, "chemokine_gradient": 1.2}


# ---------------------------------------------------------------------------
# Boundary G: wb.measure.read refusal (study_charts.render_v4_test_charts)
# ---------------------------------------------------------------------------

def test_measure_path_miss_is_a_named_refusal_not_a_dropped_series(tmp_path):
    """A behavior test whose measure.path has no image anywhere in the run's
    store must produce a refusal entry naming the missing path and the
    leaves the store DOES carry — not silently vanish from the chart list."""
    db = tmp_path / "runs.db"
    _make_runs_db(db, _STATE)
    spec = {
        "schema_version": 4,
        "tests": [
            {
                "name": "occupancy-test",
                "classification": "primary",
                "measure": {"path": "receptor.occupancy", "index": None},
                "pass_if": {},
            },
        ],
    }
    charts = render_v4_test_charts(spec, db, fallback_db=None)

    assert len(charts) == 1, "the miss must surface as an entry, not disappear"
    entry = charts[0]
    assert entry["status"] == "refused"
    assert "receptor.occupancy" in entry["missing"]
    assert "receptor.occupancy" in entry["reason"]
    assert entry["present"], "must enumerate what the store DOES carry"
    assert "recruitment_index" in entry["present"]
    assert "chemokine_gradient" in entry["present"]


def test_refusal_chart_carries_caption_and_validates(tmp_path):
    """Regression: the refusal chart must carry a ``caption`` — it is a required
    ChartPayload field, and omitting it made ``StudyChartsPayload.model_validate``
    raise, 500-ing the whole /api/study-charts payload for any study with a
    behavior test whose measure.path is not an emitted observable."""
    from vivarium_workbench.lib.models import ChartPayload

    db = tmp_path / "runs.db"
    _make_runs_db(db, _STATE)
    spec = {
        "schema_version": 4,
        "tests": [
            {"name": "occupancy-test", "measure": {"path": "receptor.occupancy"},
             "pass_if": {}},
        ],
    }
    entry = render_v4_test_charts(spec, db, fallback_db=None)[0]
    assert entry["status"] == "refused"
    assert entry.get("caption"), "refusal chart must carry a (required) caption"
    ChartPayload.model_validate(entry)  # must not raise


def test_derived_measure_refusal_is_a_clean_computed_scalar_note(tmp_path):
    """A ``kind: derived`` measure names a workspace-computed SCALAR, not an
    emitted series — the refusal says so cleanly rather than implying a data gap."""
    db = tmp_path / "runs.db"
    _make_runs_db(db, _STATE)
    spec = {
        "schema_version": 4,
        "tests": [
            {"name": "settled-t", "measure": {"kind": "derived", "path": "settled_temperature_c"},
             "pass_if": {"op": "range", "low": 40, "high": 91}},
        ],
    }
    entry = render_v4_test_charts(spec, db, fallback_db=None)[0]
    assert entry["status"] == "refused"
    assert "Computed scalar readout" in entry["caption"]
    assert "settled_temperature_c" in entry["caption"]


def test_measure_path_present_still_renders(tmp_path):
    """Regression: a measure whose path IS present in the store renders
    exactly as before — the refusal path must not fire for a hit."""
    db = tmp_path / "runs.db"
    _make_runs_db(db, _STATE)
    spec = {
        "schema_version": 4,
        "tests": [
            {
                "name": "recruitment-test",
                "classification": "primary",
                "measure": {"path": "recruitment_index", "index": None},
                "pass_if": {"op": "at_least", "value": 0.5},
            },
        ],
    }
    charts = render_v4_test_charts(spec, db, fallback_db=None)

    assert len(charts) == 1
    entry = charts[0]
    assert entry.get("status") != "refused"
    assert "svg" in entry
    assert entry["data_source"] == "study"


def test_mixed_hit_and_miss_only_the_miss_refuses(tmp_path):
    """A study with one resolvable and one unresolvable measure path: the
    resolvable one renders, the unresolvable one refuses — both survive in
    the chart list (neither silently dropped, neither wrongly refused)."""
    db = tmp_path / "runs.db"
    _make_runs_db(db, _STATE)
    spec = {
        "schema_version": 4,
        "tests": [
            {"name": "hit", "measure": {"path": "recruitment_index"}, "pass_if": {}},
            {"name": "miss", "measure": {"path": "receptor.occupancy"}, "pass_if": {}},
        ],
    }
    charts = render_v4_test_charts(spec, db, fallback_db=None)
    by_key = {c["key"]: c for c in charts}
    assert len(charts) == 2
    assert by_key["v4-hit"].get("status") != "refused"
    assert by_key["v4-miss"]["status"] == "refused"
    assert "receptor.occupancy" in by_key["v4-miss"]["missing"]


# ---------------------------------------------------------------------------
# Boundary V: wb.figure.render refusal (refresh_viz.refresh_study_viz)
# ---------------------------------------------------------------------------

def _build_study_dir(tmp_path: Path, visualizations: list) -> Path:
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    _make_runs_db(study_dir / "runs.db", _STATE)
    return study_dir


def test_figure_with_missing_requires_is_a_named_refusal(tmp_path):
    """An address-only figure entry declaring `requires: [<absent observable>]`
    must refuse with a reason naming it, instead of a bare
    `needs_manual_refresh`."""
    study_dir = _build_study_dir(tmp_path, [])
    spec = {
        "visualizations": [
            {
                "name": "ReceptorOccupancyLaw",
                "address": "local:ReceptorOccupancyLaw",
                "requires": ["receptor.occupancy"],
            },
        ],
    }
    results = refresh_study_viz(study_dir, spec, latest=None)

    assert len(results) == 1
    r = results[0]
    assert r["status"] == "refused"
    assert r["status"] != "needs_manual_refresh"
    assert "receptor.occupancy" in r["missing"]
    assert "receptor.occupancy" in r["reason"]
    assert r["present"], "must list what the run's store DOES carry"


def test_figure_with_satisfied_requires_falls_through_to_manual_refresh(tmp_path):
    """A `requires` figure whose observable IS present has no data problem —
    it still has no render command, so it keeps needs_manual_refresh (a
    wiring gap, not an absent-observable refusal)."""
    study_dir = _build_study_dir(tmp_path, [])
    spec = {
        "visualizations": [
            {
                "name": "ChemotaxisRecruitment",
                "address": "local:ChemotaxisRecruitment",
                "requires": ["recruitment_index"],
            },
        ],
    }
    results = refresh_study_viz(study_dir, spec, latest=None)

    assert len(results) == 1
    assert results[0]["status"] == "needs_manual_refresh"


def test_figure_without_requires_is_unchanged_needs_manual_refresh(tmp_path):
    """Regression / backward-compat escape hatch: an address-only entry with
    NO `requires` (the common case — self-contained/baked figures) keeps the
    exact prior behavior."""
    study_dir = _build_study_dir(tmp_path, [])
    spec = {
        "visualizations": [
            {"name": "BakedFigure", "address": "local:BakedFigure"},
        ],
    }
    results = refresh_study_viz(study_dir, spec, latest=None)

    assert results == [
        {"name": "BakedFigure", "chart": None, "status": "needs_manual_refresh"}
    ]


def test_figure_with_empty_requires_list_is_unchanged(tmp_path):
    """`requires: []` is equivalent to omitting it — no refusal machinery
    engaged."""
    study_dir = _build_study_dir(tmp_path, [])
    spec = {
        "visualizations": [
            {"name": "BakedFigure", "address": "local:BakedFigure", "requires": []},
        ],
    }
    results = refresh_study_viz(study_dir, spec, latest=None)

    assert results == [
        {"name": "BakedFigure", "chart": None, "status": "needs_manual_refresh"}
    ]

"""Unit tests for vivarium_workbench.lib.study_grade — on-demand grading of a
study's declared behavior tests against its latest completed run (the fast
"Run tests" path; no re-simulation).

Builds a minimal, self-contained fixture workspace directly in ``tmp_path``
(same lightweight pattern as ``tests/test_auto_evaluate.py``, which already
exercises this exact evaluation pipeline): a ``studies/<slug>/study.yaml``
with one behavior test over an EMITTED observable, plus a hand-written
SQLite run store (the ``history`` table schema ``RunReader`` reads directly —
see ``viva_emitters.run_reader.RunReader._sqlite_*``) so the test exercises
the REAL default evaluator path (``auto_evaluate`` -> ``study_evaluator`` ->
``RunReader``), not an injected fake runner. This only needs stock
viva-superpowers + viva-emitters — no workspace-registered evaluator or
derived-scalar seam.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from vivarium_workbench.lib import study_grade


_STUDY_YAML = """\
name: demo-study
behavior_tests:
- name: mass_in_range
  measure:
    kind: generation_average
    path: listeners.mass.cell_mass
  pass_if:
    op: range
    low: 400
    high: 600
runs:
- name: run-001
  status: completed
  emitter:
    store: run.db
"""

_STUDY_YAML_NO_RUN = """\
name: demo-study
behavior_tests:
- name: mass_in_range
  measure:
    kind: generation_average
    path: listeners.mass.cell_mass
  pass_if:
    op: range
    low: 400
    high: 600
"""


def _write_sqlite_store(db_path: Path) -> None:
    """Hand-write a minimal RunReader-openable SQLite store.

    Schema per ``viva_emitters.run_reader.RunReader._sqlite_rows``: a
    ``history`` table of ``(simulation_id, step, global_time, state)`` rows,
    ``state`` a JSON-encoded nested dict. Three ticks of
    ``listeners.mass.cell_mass`` averaging to 500 (within the test's
    [400, 600] pass_if band).
    """
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "CREATE TABLE history "
            "(simulation_id TEXT, step INTEGER, global_time REAL, state TEXT)"
        )
        for step, (t, mass) in enumerate([(0.0, 480.0), (60.0, 500.0), (120.0, 520.0)]):
            state = {"generation": 0, "listeners": {"mass": {"cell_mass": mass}}}
            con.execute(
                "INSERT INTO history VALUES (?, ?, ?, ?)",
                ("sim-0", step, t, json.dumps(state)),
            )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def ws_with_completed_run(tmp_path) -> Path:
    """Workspace with ``demo-study``: one completed run + an openable store."""
    study_dir = tmp_path / "studies" / "demo-study"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(_STUDY_YAML, encoding="utf-8")
    _write_sqlite_store(study_dir / "run.db")
    return tmp_path


@pytest.fixture
def ws_without_run(tmp_path) -> Path:
    """Workspace with ``demo-study``: behavior tests declared, no run yet."""
    study_dir = tmp_path / "studies" / "demo-study"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(_STUDY_YAML_NO_RUN, encoding="utf-8")
    return tmp_path


_STUDY_YAML_DONE_STATUS = """\
name: demo-study
behavior_tests:
- name: mass_in_range
  measure:
    kind: generation_average
    path: listeners.mass.cell_mass
  pass_if:
    op: range
    low: 400
    high: 600
runs:
- name: run-001
  status: done
  emitter:
    store: run.db
"""


@pytest.fixture
def ws_with_done_status_run(tmp_path) -> Path:
    """Workspace with a run whose status is "done" (not "completed") — the
    framework's canonical completion vocabulary treats this as complete."""
    study_dir = tmp_path / "studies" / "demo-study"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(_STUDY_YAML_DONE_STATUS, encoding="utf-8")
    _write_sqlite_store(study_dir / "run.db")
    return tmp_path


_STUDY_YAML_CANONICAL_DIVERGE = """\
name: demo-study
behavior_tests:
- name: mass_in_range
  measure:
    kind: generation_average
    path: listeners.mass.cell_mass
  pass_if:
    op: range
    low: 400
    high: 600
runs:
- name: run-001
  status: completed
  canonical: true
  timestamp: "2026-01-01T00:00:00"
  emitter:
    store: run.db
- name: run-002
  status: completed
  timestamp: "2026-06-01T00:00:00"
  emitter:
    store: run2.db
"""


@pytest.fixture
def ws_with_canonical_divergence(tmp_path) -> Path:
    """Two completed runs; the OLDER one is flagged canonical. grade_study
    must target the canonical run, not the array-last (newest) one."""
    study_dir = tmp_path / "studies" / "demo-study"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        _STUDY_YAML_CANONICAL_DIVERGE, encoding="utf-8"
    )
    _write_sqlite_store(study_dir / "run.db")
    _write_sqlite_store(study_dir / "run2.db")
    return tmp_path


def test_grade_study_with_completed_run(ws_with_completed_run):
    body, status = study_grade.grade_study(ws_with_completed_run, "demo-study")
    assert status == 200
    assert body["graded"] is True
    assert "outcome_rollup" in body
    assert body["outcome_rollup"]["PASS"] == 1
    assert body["outcome_rollup"]["total"] == 1
    assert body["run_id"] == "run-001"


def test_grade_study_no_run(ws_without_run):
    body, status = study_grade.grade_study(ws_without_run, "demo-study")
    assert status == 200
    assert body == {"graded": False, "reason": "no_run"}


def test_grade_missing_study(ws_without_run):
    body, status = study_grade.grade_study(ws_without_run, "nope")
    assert status == 404
    assert body == {"error": "study not found: nope"}


def test_grade_study_with_done_status_run(ws_with_done_status_run):
    """A run with status "done" (framework's canonical vocabulary — not the
    literal string "completed") is graded, not treated as no_run."""
    body, status = study_grade.grade_study(ws_with_done_status_run, "demo-study")
    assert status == 200
    assert body["graded"] is True
    assert body["run_id"] == "run-001"


def test_grade_study_targets_canonical_run_on_divergence(ws_with_canonical_divergence):
    """When an older run is flagged canonical:true, grade_study targets that
    run rather than the array-last (newest) completed run."""
    body, status = study_grade.grade_study(
        ws_with_canonical_divergence, "demo-study"
    )
    assert status == 200
    assert body["graded"] is True
    assert body["run_id"] == "run-001"


# ---------------------------------------------------------------------------
# Store-optional grading: a run-less (params-only) study whose tests are all
# store-less kinds (config_value / derived_scalar) grades without a run store.
# ---------------------------------------------------------------------------

_STUDY_YAML_STORELESS_NO_RUN = """\
name: params-study
conditions:
  baseline:
    params:
      my_param: 5.0
behavior_tests:
- name: param_in_range
  measure:
    kind: config_value
    field: my_param
  pass_if:
    op: ">="
    value: 4.0
"""


@pytest.fixture
def ws_storeless_no_run(tmp_path) -> Path:
    """Run-less study whose only test is a store-less config_value check."""
    study_dir = tmp_path / "studies" / "params-study"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        _STUDY_YAML_STORELESS_NO_RUN, encoding="utf-8"
    )
    return tmp_path


def test_grade_study_storeless_synthesizes_eval_run(ws_storeless_no_run):
    """A params-only study with no run grades store-less via a synthesized
    evaluation-only run — config_value reads the declared param, no store."""
    import yaml as _yaml

    body, status = study_grade.grade_study(ws_storeless_no_run, "params-study")
    assert status == 200
    assert body["graded"] is True
    assert body["outcome_rollup"]["PASS"] == 1
    assert body["outcome_rollup"]["total"] == 1
    assert body["run_id"] == "params-study-evaluation"

    # The evaluation-only run was persisted with its outcomes.
    spec = _yaml.safe_load(
        (ws_storeless_no_run / "studies" / "params-study" / "study.yaml").read_text()
    )
    runs = spec["runs"]
    assert len(runs) == 1
    ev = runs[0]
    assert ev["run_id"] == "params-study-evaluation"
    assert ev["evaluation_only"] is True
    assert ev["outcomes"]["param_in_range"]["result"] == "PASS"


def test_grade_study_storeless_is_idempotent(ws_storeless_no_run):
    """Grading twice reuses the one evaluation-only run — no duplicate rows."""
    import yaml as _yaml

    study_grade.grade_study(ws_storeless_no_run, "params-study")
    study_grade.grade_study(ws_storeless_no_run, "params-study")
    spec = _yaml.safe_load(
        (ws_storeless_no_run / "studies" / "params-study" / "study.yaml").read_text()
    )
    assert len(spec["runs"]) == 1


_STUDY_YAML_DERIVED_NO_RUN = """\
name: derived-study
behavior_tests:
- name: growth_ok
  measure:
    kind: derived_scalar
    field: my_derived
  pass_if:
    op: range
    low: 0.8
    high: 0.9
"""


def test_grade_study_no_run_tests_is_config_dict(tmp_path):
    """A run-less study whose ``tests:`` is the pytest-config DICT (not a
    behavior-test list) must not be treated as store-less-gradeable — it
    returns no_run without iterating the dict's keys (regression: that raised
    500 in the live endpoint)."""
    study_dir = tmp_path / "studies" / "cfg-study"
    study_dir.mkdir(parents=True)
    study_dir.joinpath("study.yaml").write_text(
        "name: cfg-study\n"
        "baseline: []\n"
        "tests:\n"
        "  auto_discover: true\n"
        "  data_source: latest_run\n",
        encoding="utf-8",
    )
    body, status = study_grade.grade_study(tmp_path, "cfg-study")
    assert status == 200
    assert body == {"graded": False, "reason": "no_run"}
    # No evaluation run was synthesized.
    import yaml as _yaml
    spec = _yaml.safe_load((study_dir / "study.yaml").read_text())
    assert not spec.get("runs")


def test_grade_study_storeless_derived_scalar_with_registered_computer(tmp_path, monkeypatch):
    """A run-less derived_scalar study grades store-less when the workspace
    registers a derived-scalar computer — the field resolves through the #298
    registry (via ObservableNotFound with reader=None), not the run store. This
    is the ParCa-study path: derived_scalar IS store-less-gradeable."""
    import yaml as _yaml
    import viva_superpowers.study_evaluator as se

    study_dir = tmp_path / "studies" / "derived-study"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(_STUDY_YAML_DERIVED_NO_RUN, encoding="utf-8")

    # Register a workspace derived-scalar computer for the declared field.
    monkeypatch.setattr(
        se, "load_workspace_derived_scalars",
        lambda ws_root: {"my_derived": lambda reader, test, ws: 0.85},
    )

    body, status = study_grade.grade_study(tmp_path, "derived-study")
    assert status == 200
    assert body["graded"] is True
    assert body["outcome_rollup"]["PASS"] == 1
    assert body["run_id"] == "derived-study-evaluation"

    spec = _yaml.safe_load((study_dir / "study.yaml").read_text())
    oc = spec["runs"][-1]["outcomes"]["growth_ok"]
    assert oc["result"] == "PASS"
    assert oc["measured_value"] == 0.85

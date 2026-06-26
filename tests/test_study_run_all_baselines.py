"""Tests for /api/study-run-all-baselines.

Sequences ``_post_study_run_baseline_for_test`` across every
``spec.baseline[]`` entry. The handler itself owns the per-entry
persistence, viz rendering, and run-record bookkeeping — these tests
verify the sequencing + aggregation only.
"""
import sqlite3
import yaml
import pytest

from vivarium_dashboard.lib import study_runs


@pytest.fixture
def _multi_baseline_ws(tmp_path, monkeypatch):
    """Workspace with a Study that declares 3 baseline composites."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        'schema_version: 2\nname: viva-munk\ncreated: "2026-05-14"\n'
        'plugin_version: 0.6.1\npackage_path: multi_cell\n'
    )
    sd = ws / "studies" / "compare"
    (sd / "composites").mkdir(parents=True)
    (sd / "viz").mkdir()
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "compare", "created": "2026-05-14",
        "status": "planned", "objective": "",
        "baseline": [
            {"name": "a",
             "composite": "multi_cell.composites.chemotaxis",
             "params": {"n_steps": 2}},
            {"name": "b",
             "composite": "multi_cell.composites.chemotaxis",
             "params": {"n_steps": 2}},
            {"name": "c",
             "composite": "multi_cell.composites.chemotaxis",
             "params": {"n_steps": 2}},
        ],
        "variants": [], "runs": [], "visualizations": [], "comparisons": [],
        "conclusion": None, "parent_studies": [], "interventions": [],
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_run_all_baselines_dispatches_every_entry(_multi_baseline_ws, monkeypatch):
    """The runner should call the per-entry handler once per baseline,
    passing through the requested `steps`, and aggregate the results in
    spec order. We mock the per-entry handler so the test stays
    independent of whether the workspace's composites are actually
    registered in this env (the integration path is covered by the
    existing test_study_runs.py fixture)."""
    import vivarium_dashboard.server as srv

    calls = []
    def fake_per_entry(ws_root, body):
        calls.append(body)
        return ({"simulation_id": f"run-{body['composite']}"}, 200)
    monkeypatch.setattr(study_runs, "run_study_baseline", fake_per_entry)

    from vivarium_dashboard.server import _post_study_run_all_baselines_for_test
    resp, code = _post_study_run_all_baselines_for_test(
        _multi_baseline_ws, {"study": "compare", "steps": 2})

    assert code == 200, resp
    assert [c["composite"] for c in calls] == ["a", "b", "c"]
    assert all(c["steps"] == 2 for c in calls)
    assert [r["composite"] for r in resp["results"]] == ["a", "b", "c"]
    assert resp["errors"] == []
    # Per-entry response payload is folded into each result entry.
    assert resp["results"][0]["simulation_id"] == "run-a"


def test_run_all_baselines_partial_failure_returns_207(_multi_baseline_ws, monkeypatch):
    """When some baselines succeed and others fail, the aggregate code is
    207 (multi-status) and the failures land in `errors`."""
    import vivarium_dashboard.server as srv
    def fake_per_entry(ws_root, body):
        if body["composite"] == "b":
            return ({"error": "boom"}, 500)
        return ({"simulation_id": f"run-{body['composite']}"}, 200)
    monkeypatch.setattr(study_runs, "run_study_baseline", fake_per_entry)

    from vivarium_dashboard.server import _post_study_run_all_baselines_for_test
    resp, code = _post_study_run_all_baselines_for_test(
        _multi_baseline_ws, {"study": "compare"})
    assert code == 207
    assert [r["composite"] for r in resp["results"]] == ["a", "c"]
    assert [e["composite"] for e in resp["errors"]] == ["b"]
    assert resp["errors"][0]["status"] == 500
    assert resp["errors"][0]["error"] == "boom"


def test_run_all_baselines_all_fail_propagates_first_error_code(_multi_baseline_ws, monkeypatch):
    """If no baseline succeeds, surface the first error's status code so
    the caller doesn't get a 207 for a fully-broken run."""
    import vivarium_dashboard.server as srv
    monkeypatch.setattr(study_runs, "run_study_baseline",
                        lambda ws, body: ({"error": "bad ref"}, 404))

    from vivarium_dashboard.server import _post_study_run_all_baselines_for_test
    resp, code = _post_study_run_all_baselines_for_test(
        _multi_baseline_ws, {"study": "compare"})
    assert code == 404
    assert resp["results"] == []
    assert len(resp["errors"]) == 3


def test_run_all_baselines_missing_study(_multi_baseline_ws):
    from vivarium_dashboard.server import _post_study_run_all_baselines_for_test
    resp, code = _post_study_run_all_baselines_for_test(
        _multi_baseline_ws, {"study": "nope"})
    assert code == 404


def test_run_all_baselines_no_baseline_entries(_multi_baseline_ws, tmp_path):
    """Study exists but has an empty baseline list → 400."""
    import vivarium_dashboard.server as srv
    sd = _multi_baseline_ws / "studies" / "empty"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "empty",
        "baseline": [], "variants": [], "runs": [],
        "visualizations": [], "comparisons": [],
        "interventions": [], "parent_studies": [], "conclusion": None,
    }))
    from vivarium_dashboard.server import _post_study_run_all_baselines_for_test
    resp, code = _post_study_run_all_baselines_for_test(
        _multi_baseline_ws, {"study": "empty"})
    assert code == 400
    assert "baseline" in resp["error"].lower()

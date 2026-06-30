# tests/test_study_run_dry_run.py
import pytest
from vivarium_dashboard.lib import study_runs

pytestmark = pytest.mark.usefixtures()  # uses the shared fixture workspace


def test_baseline_dry_run_resolves_without_spawn(fixture_study_ws):
    ws, study = fixture_study_ws  # a workspace with one baseline study
    resp, code = study_runs.run_study_baseline(
        ws, {"study": study, "steps": 7, "dry_run": True})
    assert code == 200
    assert resp["dry_run"] is True
    req = resp["request"]
    assert req["spec_id"]            # resolved composite id
    assert req["steps"] == 7
    assert "run_id" in req


def test_variant_dry_run_resolves_without_spawn(fixture_study_ws):
    ws, study = fixture_study_ws
    resp, code = study_runs.run_study_variant(
        ws, {"study": study, "variant": "var-one", "steps": 3, "dry_run": True})
    assert code == 200
    assert resp["dry_run"] is True
    req = resp["request"]
    assert req["spec_id"]
    assert req["steps"] == 3
    assert "run_id" in req


def test_baseline_dry_run_false_does_not_short_circuit(fixture_study_ws, monkeypatch):
    """Without dry_run the code proceeds past the guard (may fail later — that's fine)."""
    ws, study = fixture_study_ws
    # Patch resolve_study_baseline_state to raise so we know it was called
    import vivarium_dashboard.lib.study_run_state as srs
    calls = []

    def fake_resolve(*a, **kw):
        calls.append(a)
        return None, {"error": "fake error from test"}

    monkeypatch.setattr(srs, "resolve_study_baseline_state", fake_resolve)
    resp, code = study_runs.run_study_baseline(
        ws, {"study": study, "steps": 5, "dry_run": False})
    assert calls, "resolve_study_baseline_state should have been called when dry_run is False"
    assert code == 400  # the fake error propagates

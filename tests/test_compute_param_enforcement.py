"""Tests for vivarium_dashboard.server._compute_param_enforcement (D.2).

Compares a study's declared enforced_params against its latest run's applied
params (runs_meta.params_json, surfaced via spec["runs"]).
"""
import pytest

pytest.importorskip("pbg_superpowers.param_enforcement")
from vivarium_dashboard.server import _compute_param_enforcement


def test_none_when_no_enforced_params():
    assert _compute_param_enforcement({"runs": [{"params": {"te": 1}}]}) is None


def test_no_violations_when_latest_run_applies_declared():
    spec = {
        "enforced_params": {"translation_efficiency": 1},
        "runs": [
            {"run_id": "r1", "started_at": 1.0,
             "params": {"translation_efficiency": 1, "seed": 0}},
        ],
    }
    pe = _compute_param_enforcement(spec)
    assert pe["violations"] == []
    assert pe["checked_against_run"] == "r1"
    assert pe["declared"] == {"translation_efficiency": 1}


def test_missing_param_flagged():
    """The reviewer's case: TE declared, run left it at the default → absent."""
    spec = {
        "enforced_params": {"translation_efficiency": 1},
        "runs": [{"run_id": "r1", "started_at": 1.0, "params": {"seed": 0}}],
    }
    pe = _compute_param_enforcement(spec)
    assert len(pe["violations"]) == 1
    assert pe["violations"][0]["kind"] == "missing"
    assert pe["violations"][0]["param"] == "translation_efficiency"
    assert "did not set it" in pe["violations"][0]["message"]


def test_mismatch_flagged():
    spec = {
        "enforced_params": {"translation_efficiency": 1},
        "runs": [{"run_id": "r1", "started_at": 1.0,
                  "params": {"translation_efficiency": 20}}],
    }
    pe = _compute_param_enforcement(spec)
    assert pe["violations"][0]["kind"] == "mismatch"
    assert pe["violations"][0]["actual"] == 20


def test_checks_against_newest_run():
    """Latest run by started_at is the one checked."""
    spec = {
        "enforced_params": {"te": 1},
        "runs": [
            {"run_id": "old", "started_at": 1.0, "params": {"te": 20}},
            {"run_id": "new", "started_at": 9.0, "params": {"te": 1}},
        ],
    }
    pe = _compute_param_enforcement(spec)
    assert pe["checked_against_run"] == "new"
    assert pe["violations"] == []


def test_no_runs_yields_all_missing():
    spec = {"enforced_params": {"te": 1}, "runs": []}
    pe = _compute_param_enforcement(spec)
    assert pe["checked_against_run"] is None
    assert pe["violations"][0]["kind"] == "missing"

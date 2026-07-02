"""Tests for vivarium_dashboard.server._compute_param_enforcement (D.2).

Per-run param-drift check: each run is compared against the params IT was
supposed to apply (baseline run → baseline declared values; variant run →
baseline overlaid with that variant's parameter_overrides), via
pbg_superpowers.param_enforcement.resolve_run_expected. This removes the
false positive where a variant run that legitimately overrides a baseline
param was flagged against the single flat baseline dict.
"""
import pytest

pytest.importorskip("pbg_superpowers.param_enforcement")
from vivarium_dashboard.lib.study_enrichment import (
    compute_param_enforcement as _compute_param_enforcement,
)


def test_none_when_no_enforced_params():
    assert _compute_param_enforcement({"runs": [{"params": {"te": 1}}]}) is None


def test_no_violations_when_baseline_run_applies_declared():
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


# --- per-run expectation: the variant false positive is gone ---------------

_VARIANT_STUDY = {
    "enforced_params": {"a": 1, "b": 2},
    "conditions": {
        "baseline": {"composite": "base", "params": {"a": 1, "b": 2}},
        "variants": [{"name": "hi-b", "parameter_overrides": {"b": 9}}],
    },
}


def test_variant_run_correct_override_no_violation():
    """THE FIX: a variant run that correctly applied its own override (b→9) is
    NOT flagged against the baseline value b=2."""
    spec = dict(_VARIANT_STUDY)
    spec["runs"] = [
        {"run_id": "v1", "started_at": 2.0, "variant": "hi-b",
         "params": {"a": 1, "b": 9}},
    ]
    pe = _compute_param_enforcement(spec)
    assert pe["violations"] == []


def test_variant_run_wrong_override_still_caught():
    """Real drift survives: a variant run that applied the WRONG value for a
    controlled param (b=3, its declaration says 9) is still flagged."""
    spec = dict(_VARIANT_STUDY)
    spec["runs"] = [
        {"run_id": "v1", "started_at": 2.0, "variant": "hi-b",
         "params": {"a": 1, "b": 3}},
    ]
    pe = _compute_param_enforcement(spec)
    assert len(pe["violations"]) == 1
    v = pe["violations"][0]
    assert v["param"] == "b" and v["kind"] == "mismatch" and v["actual"] == 3


def test_baseline_run_drift_still_caught():
    """A baseline run that drifted from the baseline declaration is flagged."""
    spec = dict(_VARIANT_STUDY)
    spec["runs"] = [
        {"run_id": "b1", "started_at": 2.0, "params": {"a": 1, "b": 5}},
    ]
    pe = _compute_param_enforcement(spec)
    params = {v["param"]: v for v in pe["violations"]}
    assert "b" in params and params["b"]["kind"] == "mismatch"


def test_each_run_checked_against_its_own_declaration():
    """Per-run: an old baseline run that applied the wrong value is flagged,
    a clean newer run is not, and violations carry their run id."""
    spec = {
        "enforced_params": {"te": 1},
        "runs": [
            {"run_id": "old", "started_at": 1.0, "params": {"te": 20}},
            {"run_id": "new", "started_at": 9.0, "params": {"te": 1}},
        ],
    }
    pe = _compute_param_enforcement(spec)
    assert pe["checked_against_run"] == "new"  # newest is the banner's anchor
    by_run = {v["run"]: v for v in pe["violations"]}
    assert set(by_run) == {"old"}
    assert by_run["old"]["kind"] == "mismatch" and by_run["old"]["actual"] == 20


def test_no_runs_yields_all_missing():
    spec = {"enforced_params": {"te": 1}, "runs": []}
    pe = _compute_param_enforcement(spec)
    assert pe["checked_against_run"] is None
    assert pe["violations"][0]["kind"] == "missing"

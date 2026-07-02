"""Unit tests for the post-run analysis hook helpers.

These tests exercise _build_analysis_options without importing v2ecoli or
requiring a live workspace: they inject a fake ANALYSIS_REGISTRY via
monkeypatching so the pure mapping logic is tested in isolation.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_registry(name_to_scale: dict[str, str]) -> dict:
    """Build a minimal fake ANALYSIS_REGISTRY mapping name -> stub class."""
    registry = {}
    for name, scale in name_to_scale.items():
        cls = type(name, (), {"scale": scale})
        registry[name] = cls
    return registry


# ---------------------------------------------------------------------------
# Tests for _build_analysis_options
# ---------------------------------------------------------------------------

def test_single_entry_places_correct_scale():
    """A single analyses entry is placed under the correct scale key."""
    import types, sys
    fake_registry = _make_fake_registry({"ptools_rna": "single"})
    fake_mod = types.ModuleType("v2ecoli.workflow.analysis")
    fake_mod.ANALYSIS_REGISTRY = fake_registry  # type: ignore[attr-defined]
    sys.modules["v2ecoli.workflow.analysis"] = fake_mod

    from vivarium_workbench.lib.study_run_post import build_analysis_options as _build_analysis_options

    entries = [{"name": "ptools_rna", "params": {"n_tp": 8}}]
    opts, errors = _build_analysis_options(entries)

    assert errors == []
    assert "single" in opts
    assert opts["single"]["ptools_rna"] == {"n_tp": 8}


def test_multiple_entries_different_scales(monkeypatch):
    """Multiple analyses with different scales each end up in the right bucket."""
    import types, sys
    fake_registry = _make_fake_registry({
        "ptools_rna": "single",
        "central_carbon_metabolism_scatter": "multiseed",
    })
    fake_mod = types.ModuleType("v2ecoli.workflow.analysis")
    fake_mod.ANALYSIS_REGISTRY = fake_registry  # type: ignore[attr-defined]
    sys.modules["v2ecoli.workflow.analysis"] = fake_mod

    from vivarium_workbench.lib.study_run_post import build_analysis_options as _build_analysis_options

    entries = [
        {"name": "ptools_rna"},
        {"name": "central_carbon_metabolism_scatter", "params": {"color": "blue"}},
    ]
    opts, errors = _build_analysis_options(entries)

    assert errors == []
    assert opts["single"]["ptools_rna"] == {}
    assert opts["multiseed"]["central_carbon_metabolism_scatter"] == {"color": "blue"}


def test_unknown_analysis_name_records_error(monkeypatch):
    """An analysis name not in the registry produces an error, not a crash."""
    import types, sys
    fake_registry = _make_fake_registry({"ptools_rna": "single"})
    fake_mod = types.ModuleType("v2ecoli.workflow.analysis")
    fake_mod.ANALYSIS_REGISTRY = fake_registry  # type: ignore[attr-defined]
    sys.modules["v2ecoli.workflow.analysis"] = fake_mod

    from vivarium_workbench.lib.study_run_post import build_analysis_options as _build_analysis_options

    entries = [{"name": "ptools_rna"}, {"name": "does_not_exist"}]
    opts, errors = _build_analysis_options(entries)

    assert len(errors) == 1
    assert errors[0]["analysis"] == "does_not_exist"
    assert "unknown analysis" in errors[0]["error"]
    # Known analysis still present
    assert "single" in opts and "ptools_rna" in opts["single"]


def test_empty_entries_returns_empty(monkeypatch):
    """Empty analyses list returns empty options and no errors."""
    import types, sys
    fake_mod = types.ModuleType("v2ecoli.workflow.analysis")
    fake_mod.ANALYSIS_REGISTRY = {}  # type: ignore[attr-defined]
    sys.modules["v2ecoli.workflow.analysis"] = fake_mod

    from vivarium_workbench.lib.study_run_post import build_analysis_options as _build_analysis_options

    opts, errors = _build_analysis_options([])
    assert opts == {}
    assert errors == []


def test_analyses_validation_in_spec(tmp_path):
    """investigations.py: spec with valid analyses list passes validation."""
    from vivarium_workbench.lib.investigations import _validate_study_v3_or_v4

    spec = {
        "schema_version": 3,
        "name": "test-study",
        "baseline": [{"name": "bl", "composite": "my.Composite"}],
        "variants": [],
        "analyses": [
            {"name": "ptools_rna", "params": {"n_tp": 8}},
            {"name": "central_carbon_metabolism_scatter"},
        ],
    }
    # Should not raise
    _validate_study_v3_or_v4(spec)


def test_analyses_validation_bad_entry_raises():
    """investigations.py: analyses entry without a name raises."""
    from vivarium_workbench.lib.investigations import (
        _validate_study_v3_or_v4, InvestigationSpecError,
    )

    spec = {
        "schema_version": 3,
        "name": "test-study",
        "baseline": [{"name": "bl", "composite": "my.Composite"}],
        "variants": [],
        "analyses": [{"params": {"n_tp": 8}}],  # missing name
    }
    with pytest.raises(InvestigationSpecError, match="must be a mapping with a string 'name'"):
        _validate_study_v3_or_v4(spec)


def test_analyses_absent_is_valid():
    """Absent analyses field does not raise (backward compat)."""
    from vivarium_workbench.lib.investigations import _validate_study_v3_or_v4

    spec = {
        "schema_version": 3,
        "name": "test-study",
        "baseline": [{"name": "bl", "composite": "my.Composite"}],
        "variants": [],
    }
    _validate_study_v3_or_v4(spec)

"""v3 Study specs must pass load_spec validation (with empty variants)."""
import yaml
import pytest
from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError


def _write_v3(tmp_path, **overrides):
    spec = {
        "schema_version": 3,
        "name": "s1",
        "created": "2026-05-14",
        "status": "ran",
        "objective": "",
        "baseline": [{"name": "baseline", "composite": "pkg.foo", "params": {}}],
        "variants": [],
        "runs": [],
        "visualizations": [],
        "conclusion": None,
        "parent_studies": [],
    }
    spec.update(overrides)
    p = tmp_path / "study.yaml"
    p.write_text(yaml.safe_dump(spec))
    return p


def test_v3_study_with_empty_variants_validates(tmp_path):
    spec = load_spec(_write_v3(tmp_path))
    assert spec["schema_version"] == 3
    assert spec["variants"] == []


def test_v3_study_with_variants_validates(tmp_path):
    p = _write_v3(tmp_path, variants=[{"name": "hi", "intervention": {"description": "x"}}])
    spec = load_spec(p)
    assert len(spec["variants"]) == 1


def test_v3_study_missing_baseline_composite_rejected(tmp_path):
    p = _write_v3(tmp_path, baseline=[{"name": "a", "params": {}}])
    with pytest.raises(InvestigationSpecError, match="baseline"):
        load_spec(p)


def test_v3_study_bad_variant_rejected(tmp_path):
    p = _write_v3(tmp_path, variants=[{"no_name": True}])
    with pytest.raises(InvestigationSpecError, match="variants"):
        load_spec(p)


def test_v3_validation_accepts_list_baseline():
    """A v3 study with baseline as a list of {name, composite, params} validates."""
    from vivarium_dashboard.lib.investigations import _validate_study_v3
    _validate_study_v3({
        "schema_version": 3, "name": "s",
        "baseline": [{"name": "a", "composite": "pkg.a", "params": {}}],
        "variants": [], "runs": [], "visualizations": [],
    })  # must not raise


def test_v3_validation_rejects_empty_baseline_list():
    from vivarium_dashboard.lib.investigations import _validate_study_v3, InvestigationSpecError
    with pytest.raises(InvestigationSpecError):
        _validate_study_v3({
            "schema_version": 3, "name": "s",
            "baseline": [], "variants": [], "runs": [], "visualizations": [],
        })


def test_v3_validation_rejects_baseline_entry_missing_composite():
    from vivarium_dashboard.lib.investigations import _validate_study_v3, InvestigationSpecError
    with pytest.raises(InvestigationSpecError):
        _validate_study_v3({
            "schema_version": 3, "name": "s",
            "baseline": [{"name": "a", "params": {}}],
            "variants": [], "runs": [], "visualizations": [],
        })

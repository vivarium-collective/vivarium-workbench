"""v3-native Study variant handlers — add/delete variant entries in study.yaml."""
import yaml
import pytest


@pytest.fixture
def _study_ws(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "s1", "created": "2026-05-14",
        "status": "ran", "objective": "",
        "baseline": {"composite": "pkg.foo", "params": {}},
        "variants": [], "runs": [], "visualizations": [],
        "comparisons": [], "conclusion": None, "parent_studies": [],
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_variant_add_appends_to_study_yaml(_study_ws):
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    resp, code = _post_study_variant_add_for_test(_study_ws, {
        "study": "s1", "name": "hi-sens",
        "description": "triple sensitivity",
        "parameter_overrides": {"sensitivity": 6.0},
    })
    assert code == 200, resp
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert len(spec["variants"]) == 1
    v = spec["variants"][0]
    assert v["name"] == "hi-sens"
    assert v["intervention"]["parameter_overrides"] == {"sensitivity": 6.0}
    assert v["intervention"]["description"] == "triple sensitivity"


def test_variant_add_rejects_duplicate(_study_ws):
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    body = {"study": "s1", "name": "dup", "parameter_overrides": {"a": 1}}
    _post_study_variant_add_for_test(_study_ws, body)
    resp, code = _post_study_variant_add_for_test(_study_ws, body)
    assert code == 409


def test_variant_add_requires_name(_study_ws):
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    resp, code = _post_study_variant_add_for_test(_study_ws, {"study": "s1"})
    assert code == 400


def test_variant_delete_removes_entry(_study_ws):
    from vivarium_dashboard.server import (
        _post_study_variant_add_for_test, _post_study_variant_delete_for_test,
    )
    _post_study_variant_add_for_test(_study_ws, {
        "study": "s1", "name": "gone", "parameter_overrides": {"a": 1}})
    resp, code = _post_study_variant_delete_for_test(
        _study_ws, {"study": "s1", "variant": "gone"})
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["variants"] == []


def test_variant_delete_unknown(_study_ws):
    from vivarium_dashboard.server import _post_study_variant_delete_for_test
    resp, code = _post_study_variant_delete_for_test(
        _study_ws, {"study": "s1", "variant": "ghost"})
    assert code == 404

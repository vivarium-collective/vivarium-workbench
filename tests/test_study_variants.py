"""v3 study comparison handlers — comparisons CRUD in study.yaml.

Variant add/delete CRUD lives in tests/test_study_handlers.py with v3-shape tests.
This file retained for comparison-handler coverage and variant-delete edge cases.
"""
import yaml
import pytest


@pytest.fixture
def _study_ws(tmp_path, monkeypatch):
    from vivarium_workbench.lib import _root
    ws = tmp_path / "ws"
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "s1", "created": "2026-05-14",
        "status": "ran", "objective": "",
        "baseline": [{"name": "core", "composite": "pkg.composites.foo", "params": {}}],
        "variants": [], "runs": [], "visualizations": [],
        "comparisons": [], "conclusion": None, "parent_studies": [],
    }))
    _root.set_workspace_root(ws)
    return ws


def test_variant_delete_unknown(_study_ws):
    from vivarium_workbench.lib.study_crud_mutations import study_variant_delete as _post_study_variant_delete_for_test
    resp, code = _post_study_variant_delete_for_test(
        _study_ws, {"study": "s1", "variant": "ghost"})
    assert code == 404


def test_comparison_add_appends(_study_ws):
    from vivarium_workbench.lib.study_crud_mutations import study_comparison_add as _post_study_comparison_add_for_test
    resp, code = _post_study_comparison_add_for_test(_study_ws, {
        "study": "s1", "run_ids": ["r1", "r2"]})
    assert code == 200, resp
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert len(spec["comparisons"]) == 1
    assert spec["comparisons"][0]["run_ids"] == ["r1", "r2"]
    assert "name" in spec["comparisons"][0]


def test_comparison_add_requires_two_runs(_study_ws):
    from vivarium_workbench.lib.study_crud_mutations import study_comparison_add as _post_study_comparison_add_for_test
    resp, code = _post_study_comparison_add_for_test(
        _study_ws, {"study": "s1", "run_ids": ["only-one"]})
    assert code == 400

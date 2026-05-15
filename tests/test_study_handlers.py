"""Tests for the four Study-specific handlers added in Phase 1."""
import json
import yaml
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def _study_workspace(tmp_path):
    """Workspace with one minimal v3 study."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("schema_version: 2\nname: ws\ncreated: \"2026-05-13\"\nplugin_version: 0.6.1\npackage_path: pkg\n")
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "s1",
        "created": "2026-05-13",
        "status": "ran",
        "objective": "",
        "baseline": [{"name": "core", "composite": "pkg.composites.foo", "params": {}}],
        "variants": [],
        "runs": [],
        "visualizations": [],
        "conclusion": None,
        "parent_studies": [],
    }))
    return ws


def test_set_objective_updates_yaml(_study_workspace):
    from vivarium_dashboard.server import _post_study_set_objective_for_test
    body = {"study": "s1", "text": "Does X cause Y?"}
    resp, code = _post_study_set_objective_for_test(_study_workspace, body)
    assert code == 200
    spec = yaml.safe_load((_study_workspace / "studies" / "s1" / "study.yaml").read_text())
    assert spec["objective"] == "Does X cause Y?"


def test_rename_moves_directory_and_updates_name(_study_workspace):
    from vivarium_dashboard.server import _post_study_rename_for_test
    body = {"study": "s1", "new_name": "renamed-study"}
    resp, code = _post_study_rename_for_test(_study_workspace, body)
    assert code == 200
    assert (_study_workspace / "studies" / "renamed-study" / "study.yaml").is_file()
    assert not (_study_workspace / "studies" / "s1").exists()
    spec = yaml.safe_load((_study_workspace / "studies" / "renamed-study" / "study.yaml").read_text())
    assert spec["name"] == "renamed-study"


def test_rename_refuses_collision(_study_workspace):
    # Create a sibling
    (_study_workspace / "studies" / "s2").mkdir()
    (_study_workspace / "studies" / "s2" / "study.yaml").write_text("name: s2")
    from vivarium_dashboard.server import _post_study_rename_for_test
    body = {"study": "s1", "new_name": "s2"}
    resp, code = _post_study_rename_for_test(_study_workspace, body)
    assert code == 409


def test_export_returns_zip_bytes(_study_workspace):
    from vivarium_dashboard.server import _study_export_zip
    data = _study_export_zip(_study_workspace, "s1")
    # First 4 bytes of a zip file are PK\x03\x04
    assert data[:4] == b"PK\x03\x04"


def test_study_detail_page_renders(_study_workspace):
    """GET /studies/s1 — _render_study_detail_html returns HTML with key section headings."""
    from vivarium_dashboard.server import _render_study_detail_html
    import yaml
    spec = yaml.safe_load((_study_workspace / "studies" / "s1" / "study.yaml").read_text())
    html = _render_study_detail_html("s1", spec)
    assert "s1" in html
    # Should include section headings for the six cards
    assert "Baseline" in html or "baseline" in html.lower()
    assert "Objective" in html or "objective" in html.lower()
    assert "Conclusion" in html or "conclusion" in html.lower()
    assert "Variants" in html or "variants" in html.lower()


def test_variant_add_writes_flat_v3_shape(_study_workspace):
    """variant-add writes {name, base_composite, parameter_overrides} flat."""
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    resp, code = _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast", "base_composite": "core",
         "parameter_overrides": {"k": 1.5}},
    )
    assert code == 200
    spec = yaml.safe_load((_study_workspace / "studies" / "s1" / "study.yaml").read_text())
    assert spec["variants"] == [
        {"name": "fast", "base_composite": "core", "parameter_overrides": {"k": 1.5}},
    ]


def test_variant_add_default_empty_overrides(_study_workspace):
    """Omitting parameter_overrides yields {} in the stored variant."""
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    resp, code = _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast", "base_composite": "core"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_workspace / "studies" / "s1" / "study.yaml").read_text())
    assert spec["variants"][0]["parameter_overrides"] == {}


def test_variant_add_rejects_missing_base_composite(_study_workspace):
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    resp, code = _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast"},
    )
    assert code == 400
    assert "base_composite" in resp.get("error", "").lower()


def test_variant_add_rejects_unknown_base_composite(_study_workspace):
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    resp, code = _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast", "base_composite": "ghost"},
    )
    assert code == 404
    assert "base_composite" in resp.get("error", "").lower()


def test_variant_add_rejects_duplicate_name(_study_workspace):
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast", "base_composite": "core"},
    )
    resp, code = _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast", "base_composite": "core"},
    )
    assert code == 409

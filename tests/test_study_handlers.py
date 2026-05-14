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
        "baseline": {"composite": "pkg.composites.foo", "params": {}},
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


def test_set_baseline_params_updates_yaml(_study_workspace):
    from vivarium_dashboard.server import _post_study_set_baseline_params_for_test
    body = {"study": "s1", "params": {"a": 1, "n_steps": 50}}
    resp, code = _post_study_set_baseline_params_for_test(_study_workspace, body)
    assert code == 200
    spec = yaml.safe_load((_study_workspace / "studies" / "s1" / "study.yaml").read_text())
    assert spec["baseline"]["params"] == {"a": 1, "n_steps": 50}


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

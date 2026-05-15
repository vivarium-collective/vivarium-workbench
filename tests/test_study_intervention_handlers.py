"""Handler tests for v3 study intervention CRUD."""
import yaml
import pytest


@pytest.fixture
def _study_ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        'schema_version: 2\nname: ws\ncreated: "2026-05-14"\n'
        'plugin_version: 0.6.1\npackage_path: pkg\n'
    )
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "s1",
        "baseline": [{"name": "core", "composite": "pkg.composites.foo", "params": {}}],
        "variants": [], "runs": [], "visualizations": [],
        # NOTE: interventions key intentionally absent, to test default-create.
    }))
    return ws


def test_intervention_add_appends(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_add_for_test
    resp, code = _post_study_intervention_add_for_test(
        _study_ws,
        {"study": "s1", "name": "heat-shock", "description": "+10C for 5 min"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["interventions"] == [
        {"name": "heat-shock", "description": "+10C for 5 min"},
    ]


def test_intervention_add_default_empty_description(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_add_for_test
    resp, code = _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "x"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["interventions"][0]["description"] == ""


def test_intervention_add_rejects_missing_name(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_add_for_test
    resp, code = _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "description": "no name"},
    )
    assert code == 400


def test_intervention_add_rejects_duplicate_name(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_add_for_test
    _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "x", "description": "first"},
    )
    resp, code = _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "x", "description": "second"},
    )
    assert code == 409


def test_intervention_update_replaces_description(_study_ws):
    from vivarium_dashboard.server import (
        _post_study_intervention_add_for_test,
        _post_study_intervention_update_for_test,
    )
    _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "x", "description": "old"},
    )
    resp, code = _post_study_intervention_update_for_test(
        _study_ws, {"study": "s1", "name": "x", "description": "new"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["interventions"][0]["description"] == "new"


def test_intervention_update_404_unknown(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_update_for_test
    resp, code = _post_study_intervention_update_for_test(
        _study_ws, {"study": "s1", "name": "ghost", "description": "x"},
    )
    assert code == 404


def test_intervention_delete_removes(_study_ws):
    from vivarium_dashboard.server import (
        _post_study_intervention_add_for_test,
        _post_study_intervention_delete_for_test,
    )
    _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "x"},
    )
    _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "y"},
    )
    resp, code = _post_study_intervention_delete_for_test(
        _study_ws, {"study": "s1", "name": "x"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert [i["name"] for i in spec["interventions"]] == ["y"]


def test_intervention_delete_404_unknown(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_delete_for_test
    resp, code = _post_study_intervention_delete_for_test(
        _study_ws, {"study": "s1", "name": "ghost"},
    )
    assert code == 404

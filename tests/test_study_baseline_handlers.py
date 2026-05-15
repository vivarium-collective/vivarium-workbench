"""Handler tests for v3 study baseline CRUD."""
import yaml
import pytest


@pytest.fixture
def _study_ws(tmp_path):
    """Workspace with one v3 study with a single baseline entry."""
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
        "variants": [], "runs": [], "visualizations": [], "interventions": [],
    }))
    return ws


def test_baseline_add_appends(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "alt",
         "composite": "pkg.composites.bar", "params": {"k": 1}},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["baseline"] == [
        {"name": "core", "composite": "pkg.composites.foo", "params": {}},
        {"name": "alt", "composite": "pkg.composites.bar", "params": {"k": 1}},
    ]


def test_baseline_add_default_empty_params(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "alt", "composite": "pkg.composites.bar"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    alt = next(b for b in spec["baseline"] if b["name"] == "alt")
    assert alt["params"] == {}


def test_baseline_add_rejects_missing_composite(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "alt"},
    )
    assert code == 400
    assert "composite" in resp.get("error", "").lower()


def test_baseline_add_rejects_duplicate_name(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "core", "composite": "pkg.composites.other"},
    )
    assert code == 409
    assert "core" in resp.get("error", "")


def test_baseline_add_rejects_missing_name(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "composite": "pkg.composites.other"},
    )
    assert code == 400

"""v3-native Study run handlers — run baseline / variant into the Study's runs.db."""
import sqlite3
import yaml
import pytest


@pytest.fixture
def _study_ws(tmp_path, monkeypatch):
    """Workspace with one v3 study whose baseline is a real viva-munk composite."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        'schema_version: 2\nname: viva-munk\ncreated: "2026-05-14"\n'
        'plugin_version: 0.6.1\npackage_path: multi_cell\n'
    )
    sd = ws / "studies" / "s1"
    (sd / "composites").mkdir(parents=True)
    (sd / "viz").mkdir()
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "s1", "created": "2026-05-14",
        "status": "ran", "objective": "",
        "baseline": {"composite": "multi_cell.composites.chemotaxis",
                     "params": {"n_steps": 2}},
        "variants": [
            {"name": "fast", "intervention": {
                "description": "more steps",
                "parameter_overrides": {"n_steps": 3}}},
        ],
        "runs": [], "visualizations": [], "comparisons": [],
        "conclusion": None, "parent_studies": [],
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_run_baseline_persists_and_appends(_study_ws):
    from vivarium_dashboard.server import _post_study_run_baseline_for_test
    resp, code = _post_study_run_baseline_for_test(_study_ws, {"study": "s1", "steps": 2})
    assert code == 200, resp
    # runs.db got a row
    db = _study_ws / "studies" / "s1" / "runs.db"
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM runs_meta").fetchone()[0]
    conn.close()
    assert n == 1
    # study.yaml.runs grew by one, with variant=None (baseline)
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert len(spec["runs"]) == 1
    assert spec["runs"][0]["variant"] is None
    assert spec["runs"][0]["run_id"] == resp["simulation_id"]


def test_run_baseline_missing_study(_study_ws):
    from vivarium_dashboard.server import _post_study_run_baseline_for_test
    resp, code = _post_study_run_baseline_for_test(_study_ws, {"study": "nope"})
    assert code == 404

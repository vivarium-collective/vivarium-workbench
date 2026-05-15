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
        "baseline": [
            {"name": "core",
             "composite": "multi_cell.composites.chemotaxis",
             "params": {"n_steps": 2}},
        ],
        "variants": [
            {"name": "fast", "base_composite": "core",
             "parameter_overrides": {"n_steps": 3}},
        ],
        "runs": [], "visualizations": [], "comparisons": [],
        "conclusion": None, "parent_studies": [], "interventions": [],
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


def test_run_variant_layers_overrides(_study_ws):
    from vivarium_dashboard.server import _post_study_run_variant_for_test
    resp, code = _post_study_run_variant_for_test(
        _study_ws, {"study": "s1", "variant": "fast"})
    assert code == 200, resp
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    # The new run records the variant name.
    variant_runs = [r for r in spec["runs"] if r.get("variant") == "fast"]
    assert len(variant_runs) == 1
    assert variant_runs[0]["run_id"] == resp["simulation_id"]


def test_run_variant_unknown_variant(_study_ws):
    from vivarium_dashboard.server import _post_study_run_variant_for_test
    resp, code = _post_study_run_variant_for_test(
        _study_ws, {"study": "s1", "variant": "ghost"})
    assert code == 404


def _seed_run(study_ws, run_id, variant=None):
    """Helper: put one run row in the Study's runs.db + study.yaml."""
    import sqlite3
    from vivarium_dashboard.lib.composite_runs import connect
    sd = study_ws / "studies" / "s1"
    db = sd / "runs.db"
    conn = connect(db)
    conn.execute(
        "INSERT INTO runs_meta (run_id, spec_id, label, params_json, "
        "started_at, status) VALUES (?,?,?,?,?,?)",
        (run_id, "pkg.foo", "lbl", "{}", 1.0, "completed"),
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history (simulation_id TEXT, step INTEGER, "
        "global_time REAL, state TEXT, PRIMARY KEY (simulation_id, step))"
    )
    conn.execute("INSERT INTO history VALUES (?,?,?,?)", (run_id, 0, 0.0, "{}"))
    conn.commit()
    conn.close()
    sf = sd / "study.yaml"
    spec = yaml.safe_load(sf.read_text())
    spec.setdefault("runs", []).append(
        {"run_id": run_id, "variant": variant, "label": "lbl", "status": "completed"})
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))


def test_run_delete_removes_from_db_and_yaml(_study_ws):
    from vivarium_dashboard.server import _post_study_run_delete_for_test
    _seed_run(_study_ws, "r1")
    _seed_run(_study_ws, "r2")
    resp, code = _post_study_run_delete_for_test(
        _study_ws, {"study": "s1", "run_id": "r1"})
    assert code == 200
    import sqlite3
    conn = sqlite3.connect(str(_study_ws / "studies" / "s1" / "runs.db"))
    meta_ids = [r[0] for r in conn.execute("SELECT run_id FROM runs_meta")]
    hist_ids = [r[0] for r in conn.execute("SELECT DISTINCT simulation_id FROM history")]
    conn.close()
    assert meta_ids == ["r2"]
    assert hist_ids == ["r2"]
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert [r["run_id"] for r in spec["runs"]] == ["r2"]


def test_runs_clear_empties_everything(_study_ws):
    from vivarium_dashboard.server import _post_study_runs_clear_for_test
    _seed_run(_study_ws, "r1")
    _seed_run(_study_ws, "r2")
    resp, code = _post_study_runs_clear_for_test(_study_ws, {"study": "s1"})
    assert code == 200
    import sqlite3
    conn = sqlite3.connect(str(_study_ws / "studies" / "s1" / "runs.db"))
    n_meta = conn.execute("SELECT COUNT(*) FROM runs_meta").fetchone()[0]
    n_hist = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    conn.close()
    assert n_meta == 0 and n_hist == 0
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["runs"] == []


def test_run_delete_missing_study(_study_ws):
    from vivarium_dashboard.server import _post_study_run_delete_for_test
    resp, code = _post_study_run_delete_for_test(_study_ws, {"study": "nope", "run_id": "r1"})
    assert code == 404


def test_run_baseline_with_explicit_composite_404s_unknown_name(_study_ws):
    """Body's `composite` selects a baseline entry by name; unknown → 404."""
    from vivarium_dashboard.server import _post_study_run_baseline_for_test
    resp, code = _post_study_run_baseline_for_test(
        _study_ws, {"study": "s1", "composite": "no-such-name"})
    assert code == 404
    assert "composite" in resp.get("error", "").lower()


def test_run_baseline_no_baseline_400s():
    """Empty baseline list → 400 with 'no baseline' error."""
    import tempfile
    from pathlib import Path
    from vivarium_dashboard.server import _post_study_run_baseline_for_test
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "workspace.yaml").write_text(
            'schema_version: 2\nname: t\ncreated: "2026-05-14"\n'
            'plugin_version: 0.6.1\npackage_path: t\n'
        )
        sd = ws / "studies" / "empty"
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 3, "name": "empty",
            "baseline": [], "variants": [],
            "runs": [], "visualizations": [],
        }))
        resp, code = _post_study_run_baseline_for_test(ws, {"study": "empty"})
        assert code == 400
        assert "baseline" in resp.get("error", "").lower()


def test_run_variant_layers_v3_overrides():
    """A v3 variant with base_composite + parameter_overrides resolves and layers."""
    import tempfile
    from pathlib import Path
    from vivarium_dashboard.server import _post_study_run_variant_for_test
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "workspace.yaml").write_text(
            'schema_version: 2\nname: t\ncreated: "2026-05-14"\n'
            'plugin_version: 0.6.1\npackage_path: nopkg\n'
        )
        sd = ws / "studies" / "s2"
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 3, "name": "s2",
            "baseline": [
                {"name": "core",
                 "composite": "nopkg.composites.missing",
                 "params": {"k": 1, "n_steps": 2}},
            ],
            "variants": [
                {"name": "fast", "base_composite": "core",
                 "parameter_overrides": {"k": 2, "n_steps": 3}},
            ],
            "runs": [], "visualizations": [], "interventions": [],
        }))
        resp, code = _post_study_run_variant_for_test(
            ws, {"study": "s2", "variant": "fast"})
        # Composite is missing in this fake pkg → expect 400 from
        # _resolve_study_baseline_state, NOT a 400 about base_composite shape.
        assert code == 400
        err = resp.get("error", "")
        assert "base_composite" not in err.lower()
        assert "no baseline" not in err.lower()


def test_run_variant_unknown_base_composite_404s():
    """Variant referencing a non-existent baseline name → 404."""
    import tempfile
    from pathlib import Path
    from vivarium_dashboard.server import _post_study_run_variant_for_test
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "workspace.yaml").write_text(
            'schema_version: 2\nname: t\ncreated: "2026-05-14"\n'
            'plugin_version: 0.6.1\npackage_path: nopkg\n'
        )
        sd = ws / "studies" / "s3"
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 3, "name": "s3",
            "baseline": [{"name": "core", "composite": "nopkg.x", "params": {}}],
            "variants": [{"name": "dangling", "base_composite": "ghost",
                          "parameter_overrides": {}}],
            "runs": [], "visualizations": [],
        }))
        resp, code = _post_study_run_variant_for_test(
            ws, {"study": "s3", "variant": "dangling"})
        assert code == 404
        assert "base_composite" in resp.get("error", "").lower()

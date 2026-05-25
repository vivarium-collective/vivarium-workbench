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


@pytest.mark.xfail(
    reason="needs multi_cell.composites.chemotaxis registered in the test env "
           "(currently 400s with 'composite not found in registry or file-discovery "
           "index'). Either install the multi_cell package alongside this test or "
           "rewrite the fixture to use a composite that ships with the dashboard "
           "self-tests.",
    strict=False,
)
def test_run_baseline_persists_to_runs_db_canonical(_study_ws):
    """F2: runs.db is the canonical source of truth — the runs_meta row
    holds the full record (params, started_at, completed_at, etc.). The
    dashboard no longer ALSO appends to study.yaml.runs[]; that field
    stays untouched. See _count_runs_for_study for how the read side now
    merges runs.db + spec.runs for back-compat counts."""
    from vivarium_dashboard.server import _post_study_run_baseline_for_test
    resp, code = _post_study_run_baseline_for_test(_study_ws, {"study": "s1", "steps": 2})
    assert code == 200, resp

    # Canonical: runs_meta row exists with the right run_id and sim_name.
    db = _study_ws / "studies" / "s1" / "runs.db"
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT run_id, sim_name FROM runs_meta"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == resp["simulation_id"]

    # F2 contract: study.yaml.runs[] is NOT appended to. The runs.db row
    # is the canonical record; duplicating into yaml lets the two drift.
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec.get("runs", []) == [], (
        f"F2: study.yaml.runs[] must not grow on run; got {spec.get('runs')}"
    )


def test_run_baseline_missing_study(_study_ws):
    from vivarium_dashboard.server import _post_study_run_baseline_for_test
    resp, code = _post_study_run_baseline_for_test(_study_ws, {"study": "nope"})
    assert code == 404


@pytest.mark.xfail(
    reason="needs multi_cell.composites.chemotaxis registered in the test env "
           "(same fixture limitation as test_run_baseline_persists_to_runs_db_canonical).",
    strict=False,
)
def test_run_variant_layers_overrides(_study_ws):
    """F2: variant run lands in runs_meta with the variant name as sim_name
    (params_json captures the override). study.yaml.runs[] is NOT appended."""
    from vivarium_dashboard.server import _post_study_run_variant_for_test
    resp, code = _post_study_run_variant_for_test(
        _study_ws, {"study": "s1", "variant": "fast"})
    assert code == 200, resp

    # Canonical: runs_meta row carries variant identity via sim_name + label.
    db = _study_ws / "studies" / "s1" / "runs.db"
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT run_id, sim_name, label FROM runs_meta"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == resp["simulation_id"]
    assert rows[0][1] == "fast"  # sim_name
    assert rows[0][2] == "fast"  # label

    # F2 contract: yaml stays clean.
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec.get("runs", []) == []


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


# ----------------------------------------------------------------------------
# F2 — _count_runs_for_study (canonical reader; merges runs.db + spec.runs)
# ----------------------------------------------------------------------------


def test_count_runs_for_study_reads_runs_db_first(tmp_path, monkeypatch):
    """The canonical source is runs.db; spec.runs is a back-compat fallback.
    When runs.db has more rows than spec.runs, the higher count wins —
    F2's whole point is that runs landing via pbg_runner (or any future
    CLI runner) show up without a corresponding study.yaml entry."""
    import vivarium_dashboard.server as srv
    from vivarium_dashboard.lib.composite_runs import connect

    ws = tmp_path / "ws"
    sd = ws / "studies" / "demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "demo",
        "baseline": [{"name": "b1", "composite": "pkg.x"}],
        # No runs: entry — workspace post-F2 doesn't populate this.
    }))
    conn = connect(sd / "runs.db")
    for rid in ("r1", "r2", "r3"):
        conn.execute(
            "INSERT INTO runs_meta (run_id, spec_id, started_at, status, sim_name) "
            "VALUES (?, 'pkg.x', 1.0, 'completed', ?)", (rid, rid),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(srv, "WORKSPACE", ws)
    spec = yaml.safe_load((sd / "study.yaml").read_text())
    assert srv._count_runs_for_study("demo", spec) == 3


def test_count_runs_for_study_fallback_to_spec_runs(tmp_path, monkeypatch):
    """Legacy v3 spec with historical runs[] entries and no runs.db —
    the count still reflects the legacy entries. This keeps the Studies
    tab honest when migrating a workspace that has yaml-only run records."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    sd = ws / "studies" / "legacy"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "legacy",
        "baseline": [{"name": "b1", "composite": "pkg.x"}],
        "runs": [
            {"run_id": "old-1", "status": "completed"},
            {"run_id": "old-2", "status": "completed"},
        ],
    }))
    # No runs.db file on disk.
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    spec = yaml.safe_load((sd / "study.yaml").read_text())
    assert srv._count_runs_for_study("legacy", spec) == 2


def test_count_runs_for_study_zero_when_neither(tmp_path, monkeypatch):
    """No runs.db and no spec.runs → 0. Doesn't crash on missing study dir."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    sd = ws / "studies" / "empty"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "empty",
        "baseline": [{"name": "b1", "composite": "pkg.x"}],
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    spec = yaml.safe_load((sd / "study.yaml").read_text())
    assert srv._count_runs_for_study("empty", spec) == 0


def test_count_runs_for_study_takes_max_when_both_present(tmp_path, monkeypatch):
    """During a workspace's migration window both sources may coexist with
    overlapping run_ids. The helper returns the larger count so the
    dashboard never undercounts. (Exact dedupe by run_id is overkill here;
    counts are display-only.)"""
    import vivarium_dashboard.server as srv
    from vivarium_dashboard.lib.composite_runs import connect

    ws = tmp_path / "ws"
    sd = ws / "studies" / "mixed"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "mixed",
        "baseline": [{"name": "b1", "composite": "pkg.x"}],
        "runs": [
            {"run_id": "a", "status": "completed"},
            {"run_id": "b", "status": "completed"},
            {"run_id": "c", "status": "completed"},
        ],
    }))
    conn = connect(sd / "runs.db")
    # Only 2 in db; legacy yaml has 3. Helper returns max(2, 3) = 3.
    for rid in ("a", "b"):
        conn.execute(
            "INSERT INTO runs_meta (run_id, spec_id, started_at, status, sim_name) "
            "VALUES (?, 'pkg.x', 1.0, 'completed', ?)", (rid, rid),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(srv, "WORKSPACE", ws)
    spec = yaml.safe_load((sd / "study.yaml").read_text())
    assert srv._count_runs_for_study("mixed", spec) == 3

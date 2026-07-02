"""Route-level smoke coverage for the FastAPI routes re-exposed after the
stdlib ``server.py`` retirement.

The stdlib server served a family of ``/api/study-*`` aliases + v3-native study
routes and six ``DELETE`` routes that the FastAPI app never re-implemented, so
the live UI/clients 404'd (or 405'd) on them.  These tests prove each route is
registered, reaches its ``lib`` builder, and returns the builder's documented
status semantics.  The builders themselves are covered by dedicated
``lib``-level tests (test_compare_group_mutations_lib, test_scaffold_mutations_lib,
test_composite_mutations_lib, test_viz_commit_mutations_lib, test_analysis_outputs,
test_study_sync_runs_endpoint, …); here we assert the wiring, not the logic.
"""
import yaml
from fastapi.testclient import TestClient

from vivarium_dashboard.api import app as appmod


def _client(ws):
    app = appmod.create_app()
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    return TestClient(app)


def _study_ws(tmp_path, slug="demo-study", *, spec_extra=None):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: gap-test\n")
    (ws / ".pbg").mkdir()
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    spec = {
        "schema_version": 4,
        "name": slug,
        "question": "does the route resolve?",
        "conditions": {"baseline": {"composite": "x.y.z", "params": {"n_steps": 5}}},
    }
    if spec_extra:
        spec.update(spec_extra)
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    return ws, slug


# ---------------------------------------------------------------------------
# GROUP A — study-* comparison / group routes
# ---------------------------------------------------------------------------


def test_study_comparison_add_happy(tmp_path):
    ws, slug = _study_ws(tmp_path)
    # NB: study_comparison_add treats `name` as a study-id alias first (its
    # dual-purpose contract), so send only `study` + `run_ids`; the comparison
    # label is auto-generated.
    r = _client(ws).post(
        "/api/study-comparison-add",
        json={"study": slug, "run_ids": ["r1", "r2"]},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["name"].startswith("comparison-")


def test_study_comparison_add_missing_study_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).post("/api/study-comparison-add", json={"run_ids": ["a", "b"]})
    assert r.status_code == 400


def test_study_comparison_update_not_found_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).post(
        "/api/study-comparison-update",
        json={"investigation": "nope", "name": "cmp", "fields_to_update": {}},
    )
    assert r.status_code == 404


def test_study_group_add_happy(tmp_path):
    ws, slug = _study_ws(tmp_path, spec_extra={
        "variants": [{"name": "v1"}, {"name": "v2"}],
    })
    r = _client(ws).post(
        "/api/study-group-add",
        json={"investigation": slug, "name": "grp", "variants": ["v1", "v2"]},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_study_group_update_not_found_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).post(
        "/api/study-group-update",
        json={"investigation": "nope", "name": "grp", "fields_to_update": {}},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GROUP B — study-* POST routes (aliases + v3-native)
# ---------------------------------------------------------------------------


def test_study_delete_happy(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).post("/api/study-delete", json={"name": slug})
    assert r.status_code == 200
    assert not (ws / "studies" / slug).exists()


def test_study_delete_unknown_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).post("/api/study-delete", json={"name": "ghost"})
    assert r.status_code == 404


def test_study_set_observables_missing_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).post("/api/study-set-observables", json={"paths": []})
    assert r.status_code == 400


def test_study_set_conclusion_happy(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).post(
        "/api/study-set-conclusion", json={"investigation": slug, "markdown": "done"}
    )
    assert r.status_code == 200


def test_study_set_description_happy(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).post(
        "/api/study-set-description",
        json={"investigation": slug, "fields": {"question": "why?"}},
    )
    assert r.status_code == 200


def test_study_set_observables_happy(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).post(
        "/api/study-set-observables",
        json={"investigation": slug, "paths": [["a", "b"]]},
    )
    assert r.status_code == 200


def test_study_variant_rebuild_missing_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).post("/api/study-variant-rebuild", json={})
    assert r.status_code in (400, 404)


def test_study_viz_add_unknown_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).post(
        "/api/study-viz-add",
        json={"investigation": "ghost", "name": "v1", "address": "pkg.viz.x"},
    )
    assert r.status_code == 404


def test_study_viz_render_unknown_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).post("/api/study-viz-render", json={"name": "ghost"})
    assert r.status_code in (400, 404)


def test_study_sync_runs_reaches_lib(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).post("/api/study-sync-runs", json={"study": slug})
    # Reaches the lib builder (not a 404 route / 405); a study with no runs.db
    # is a normal success.
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GROUP B — study-* GET routes
# ---------------------------------------------------------------------------


def test_study_viz_html_missing_param_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).get("/api/study-viz-html", params={"study": "demo-study"})
    # missing run_id -> 400 per lib contract
    assert r.status_code == 400


def test_study_viz_html_happy_empty(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).get(
        "/api/study-viz-html", params={"study": slug, "run_id": "run-1"}
    )
    assert r.status_code == 200
    assert r.json()["viz_files"] == []


def test_study_composites_happy(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).get("/api/study-composites", params={"study": slug})
    assert r.status_code == 200
    assert "composites" in r.json()


def test_study_composites_missing_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).get("/api/study-composites")
    assert r.status_code == 400


def test_study_state_tree_missing_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).get("/api/study-state-tree", params={"study": "demo-study"})
    # missing composite -> 400
    assert r.status_code == 400


def test_study_bigraph_paths_missing_slug_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).get("/api/study-bigraph-paths")
    assert r.status_code == 400


def test_study_bigraph_paths_no_baseline_400(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).get("/api/study-bigraph-paths", params={"study": slug})
    # study.yaml has no baseline[] entries -> 400 per lib contract
    assert r.status_code == 400


def test_study_analysis_outputs_happy_empty(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).get("/api/study-analysis-outputs", params={"study": slug})
    assert r.status_code == 200
    assert r.json()["study"] == slug
    assert r.json()["files"] == []


def test_study_analysis_outputs_unknown_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).get("/api/study-analysis-outputs", params={"study": "ghost"})
    assert r.status_code == 404


def test_study_analysis_file_missing_400(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).get("/api/study-analysis-file", params={"study": slug})
    # missing/empty path -> DownloadError (400/404)
    assert r.status_code in (400, 404)


def test_study_analysis_zip_happy(tmp_path):
    ws, slug = _study_ws(tmp_path)
    r = _client(ws).get("/api/study-analysis-zip", params={"study": slug})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"


# ---------------------------------------------------------------------------
# GROUP C — DELETE routes
# ---------------------------------------------------------------------------


def test_delete_investigation_composite_missing_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).request("DELETE", "/api/investigation-composite", json={})
    assert r.status_code == 400


def test_delete_investigation_composite_happy(tmp_path):
    ws, slug = _study_ws(tmp_path, spec_extra={"composites": [{"name": "c1"}]})
    r = _client(ws).request(
        "DELETE",
        "/api/investigation-composite",
        json={"investigation": slug, "name": "c1"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_investigation_comparison_missing_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).request("DELETE", "/api/investigation-comparison", json={})
    assert r.status_code == 400


def test_delete_investigation_comparison_happy(tmp_path):
    ws, slug = _study_ws(tmp_path, spec_extra={"comparisons": [{"name": "cmp"}]})
    r = _client(ws).request(
        "DELETE",
        "/api/investigation-comparison",
        json={"investigation": slug, "name": "cmp"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_investigation_group_missing_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).request("DELETE", "/api/investigation-group", json={})
    assert r.status_code == 400


def test_delete_investigation_group_happy(tmp_path):
    ws, slug = _study_ws(tmp_path, spec_extra={"groups": [{"name": "grp"}]})
    r = _client(ws).request(
        "DELETE",
        "/api/investigation-group",
        json={"investigation": slug, "name": "grp"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_simulation_missing_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).request("DELETE", "/api/simulation", json={})
    assert r.status_code == 400


def test_delete_simulation_happy(tmp_path):
    ws, _ = _study_ws(tmp_path)
    (ws / "workspace.yaml").write_text(
        yaml.safe_dump({"name": "gap-test", "simulations": [{"name": "sim1"}]})
    )
    r = _client(ws).request("DELETE", "/api/simulation", json={"name": "sim1"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    reloaded = yaml.safe_load((ws / "workspace.yaml").read_text())
    assert "simulations" not in reloaded


def test_delete_simulation_unknown_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).request("DELETE", "/api/simulation", json={"name": "ghost"})
    assert r.status_code == 404


def test_delete_visualization_happy(tmp_path):
    ws, _ = _study_ws(tmp_path)
    (ws / "workspace.yaml").write_text(
        yaml.safe_dump({"name": "gap-test", "visualizations": [{"name": "viz1"}]})
    )
    r = _client(ws).request("DELETE", "/api/visualization", json={"name": "viz1"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_visualization_unknown_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).request("DELETE", "/api/visualization", json={"name": "ghost"})
    assert r.status_code == 404


def test_delete_simulation_run_missing_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).request("DELETE", "/api/simulation-run", json={})
    assert r.status_code == 400


def test_delete_simulation_run_unknown_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    r = _client(ws).request(
        "DELETE", "/api/simulation-run", json={"run_id": "no-such-run"}
    )
    assert r.status_code == 404

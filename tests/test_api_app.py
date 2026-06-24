"""Tests for the FastAPI seam (vivarium_dashboard.api.app)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vivarium_dashboard.api import app as api_app
from vivarium_dashboard.api.app import create_app, get_workspace


@pytest.fixture
def client(tmp_path) -> TestClient:
    """A TestClient whose workspace is an (empty) tmp dir by default."""
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: tmp_path
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_simulations_empty_workspace(client):
    """An empty workspace yields the typed empty payload, not a 500."""
    r = client.get("/api/simulations")
    assert r.status_code == 200
    assert r.json() == {"simulations": [], "current": None}


def test_simulations_returns_typed_rows(client, monkeypatch):
    """Rows from list_simulations are validated through SimRow on the way out."""
    row = {
        "run_id": "r1", "spec_id": "baseline", "sim_name": "sim", "label": "Run 1",
        "status": "completed", "n_steps": 10, "progress_step": 10,
        "started_at": 1700000000.0, "completed_at": None, "db_path": "/ws/runs.db",
        "emitter": "xarray", "studies": [], "study_slug": "study-a",
        "investigation_slug": None, "remote_origin": None,
    }
    monkeypatch.setattr(api_app, "list_simulations", lambda ws: [row])
    r = client.get("/api/simulations")
    assert r.status_code == 200
    body = r.json()
    assert body["current"] is None
    assert len(body["simulations"]) == 1
    sim = body["simulations"][0]
    assert sim["run_id"] == "r1"
    assert sim["emitter"] == "xarray"
    assert sim["started_at"] == 1700000000.0   # epoch float survives the round-trip


def test_openapi_includes_typed_models(client):
    """The pydantic response models surface in the OpenAPI schema (the payoff:
    a machine-readable contract the TS client can later be generated from)."""
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    assert "SimulationsPayload" in components
    assert "SimRow" in components
    # SimRow.started_at typed as number (epoch float), not string:
    assert components["SimRow"]["properties"]["started_at"]["type"] == "number"


def test_config_route(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json() == {"mode": "local-server", "basePath": None}


def test_iset_list_empty_workspace(client):
    r = client.get("/api/iset-list")
    assert r.status_code == 200
    assert r.json() == []


def test_iset_list_typed_passthrough(client, monkeypatch):
    """The route validates the builder's output through InvestigationSummary —
    including the untyped `lifecycle` passthrough (not stripped) and the minimal
    {name, error} parse-failure variant."""
    summaries = [
        {"name": "inv-a", "title": "Inv A", "status": "active",
         "effective_status": "in-progress", "description": "d", "question": "q",
         "hypothesis": "h", "n_studies": 2, "studies": ["s1", "s2"],
         "lifecycle": {"phase": "run", "extra": 1}, "current": True},
        {"name": "inv-bad", "error": "parse failed: boom"},
    ]
    monkeypatch.setattr(
        api_app.investigation_status, "build_iset_summary",
        lambda ws, **kw: summaries,
    )

    body = client.get("/api/iset-list").json()
    assert body[0]["studies"] == ["s1", "s2"]
    assert body[0]["lifecycle"] == {"phase": "run", "extra": 1}   # Any field: not stripped
    assert body[0]["current"] is True
    assert body[1]["name"] == "inv-bad" and body[1]["error"] == "parse failed: boom"
    assert body[1]["studies"] == [] and body[1]["lifecycle"] is None


def test_data_sources_no_provider(client, tmp_path):
    """A workspace with no data-source provider yields the typed empty bundle."""
    (tmp_path / "workspace.yaml").write_text("{}")
    r = client.get("/api/data-sources")
    assert r.status_code == 200
    assert r.json()["sources"] == []


def test_data_sources_typed(client, monkeypatch):
    payload = {"label": "My data", "sources": [
        {"key": "k1", "path": "/p", "category": "genome", "kind": "local",
         "size_bytes": 42, "url": "http://x"}]}
    monkeypatch.setattr(api_app._data_sources, "enumerate_data_sources",
                        lambda ws, **kw: payload)
    body = client.get("/api/data-sources").json()
    assert body["label"] == "My data"
    assert body["sources"][0]["key"] == "k1"
    assert body["sources"][0]["size_bytes"] == 42


def test_references_bib_preserves_extra_fields(client, monkeypatch):
    """BibEntry uses extra='allow', so arbitrary bibtex fields survive the typed
    response (FastAPI does not strip them) — only `key` is required."""
    import vivarium_dashboard.lib.references_fetch as rf
    import vivarium_dashboard.lib.report as report_mod

    entries = [{"key": "smith2020", "title": "T", "author": "Smith",
                "publisher": "ACME", "weird_field": "xyz"}]
    monkeypatch.setattr(report_mod, "_parse_bib_entries", lambda ws: entries)
    monkeypatch.setattr(rf, "load_cache", lambda ws: {})
    monkeypatch.setattr(rf, "enrich_entries", lambda e, c: e)

    e = client.get("/api/references-bib").json()["entries"][0]
    assert e["key"] == "smith2020"
    assert e["publisher"] == "ACME"     # unknown bibtex field preserved
    assert e["weird_field"] == "xyz"    # preserved, not stripped


def test_saved_visualizations_empty(client):
    """An empty workspace yields the typed empty bundle (no studies)."""
    b = client.get("/api/saved-visualizations").json()
    assert b["saved"] == []
    assert b["report_cards"] == []
    assert b["ptools"]["studies"] == []


def test_saved_visualizations_typed(client, monkeypatch):
    payload = {
        "parsimony_available": True,
        "saved": [{"study": "s1", "name": "ecoli_3d",
                   "pack_url": "/studies/s1/viz/3d/ecoli_3d.pack.json",
                   "meta_url": None, "n_placed": 1200, "created": 1700000000,
                   "viewer_url": "http://x"}],
        "ptools": {"configured": True, "studies": [{"study": "s1", "n_tsvs": 3}]},
        "report_cards": [{"study": "s1", "name": "rc",
                          "url": "/studies/s1/viz/report_card/rc.html",
                          "verdict": "PASS", "created": 1700000001}],
    }
    monkeypatch.setattr(api_app._saved_viz, "build_saved_visualizations",
                        lambda ws: payload)
    b = client.get("/api/saved-visualizations").json()
    assert b["parsimony_available"] is True
    sv = b["saved"][0]
    assert sv["name"] == "ecoli_3d" and sv["n_placed"] == 1200
    assert sv["viewer_url"] == "http://x"
    assert b["ptools"]["studies"][0]["n_tsvs"] == 3
    assert b["report_cards"][0]["verdict"] == "PASS"


def test_new_routes_in_openapi(client):
    components = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("DashConfig", "InvestigationSummary", "DataSourcesPayload",
                 "DataSource", "BibEntry", "ReferencesBibPayload",
                 "SavedVisualizationsPayload", "SavedViz", "ReportCard"):
        assert name in components


def test_workspace_default_is_cwd(monkeypatch):
    """get_workspace honors the env var and defaults to cwd."""
    monkeypatch.delenv("VIVARIUM_DASHBOARD_WORKSPACE", raising=False)
    assert get_workspace() == Path(".").resolve()
    monkeypatch.setenv("VIVARIUM_DASHBOARD_WORKSPACE", "/tmp/ws-xyz")
    assert get_workspace() == Path("/tmp/ws-xyz").resolve()


def test_study_charts_empty_workspace(client):
    """A study with no runs.db / charts yields the typed empty payload, not a 500."""
    r = client.get("/api/study-charts/dnaa-1")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "study": "dnaa-1", "schema_version": None, "charts": [],
        "db_exists": False, "static_count": 0, "live_count": 0,
    }


def test_study_charts_validates_polymorphic_charts(client, monkeypatch):
    """Live (svg) and static (img) charts both pass through the typed response."""
    payload = {
        "study": "dnaa-1", "schema_version": 4,
        "charts": [
            {"key": "live1", "title": "Live", "caption": "c", "svg": "<svg/>", "source": "live"},
            {"key": "stat1", "title": "Static", "caption": "c", "img": "data:image/png;base64,AA",
             "source": "static", "media": "png", "freshness": "fresh"},
        ],
        "db_exists": True, "static_count": 1, "live_count": 1,
    }
    monkeypatch.setattr(api_app, "build_study_charts_payload", lambda ws, slug: payload)
    body = client.get("/api/study-charts/dnaa-1").json()
    assert body["live_count"] == 1 and body["static_count"] == 1
    assert body["charts"][0]["svg"] == "<svg/>" and body["charts"][0]["img"] is None
    assert body["charts"][1]["img"].startswith("data:image/png") and body["charts"][1]["svg"] is None


def test_study_charts_in_openapi(client):
    """The study-charts route + its models appear in the generated OpenAPI schema."""
    spec = client.get("/openapi.json").json()
    assert "/api/study-charts/{slug}" in spec["paths"]
    for name in ("StudyChartsPayload", "ChartPayload"):
        assert name in spec["components"]["schemas"]


def test_swagger_ui_and_redoc_served(client):
    """The auto-generated docs pages are reachable (the whole point of the seam)."""
    assert client.get("/docs").status_code == 200          # Swagger UI HTML
    assert client.get("/redoc").status_code == 200         # ReDoc HTML
    assert client.get("/openapi.json").status_code == 200  # raw schema


# ---------------------------------------------------------------------------
# /api/visualization-classes
# ---------------------------------------------------------------------------

def test_visualization_classes_empty_workspace(client, monkeypatch):
    """An empty workspace (no workspace.yaml / no core module) yields the typed
    payload — not a 500.  We patch ``list_visualization_classes`` to return
    empty so the assertion is deterministic regardless of which pbg packages are
    installed in the test environment."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(_app, "list_visualization_classes", lambda ws: {"classes": []})
    r = client.get("/api/visualization-classes")
    assert r.status_code == 200
    assert r.json() == {"classes": []}


def test_visualization_classes_typed_passthrough(client, monkeypatch):
    """The route validates the builder's output through VisualizationClassesPayload;
    extra fields on each VizClass entry are preserved (extra='allow')."""
    import vivarium_dashboard.api.app as _app

    payload = {
        "classes": [
            {"address": "local:TimeSeriesPlot", "name": "TimeSeriesPlot",
             "doc": "A time-series plot.", "kind": "visualization",
             "extra_meta": "kept"},
            {"address": "local:v2ecoli.workflow.analysis.GrowthAnalysis",
             "name": "GrowthAnalysis", "doc": "Growth analysis.", "kind": "analysis"},
        ]
    }
    monkeypatch.setattr(_app, "list_visualization_classes", lambda ws: payload)
    r = client.get("/api/visualization-classes")
    assert r.status_code == 200
    body = r.json()
    assert len(body["classes"]) == 2
    c0 = body["classes"][0]
    assert c0["name"] == "TimeSeriesPlot"
    assert c0["kind"] == "visualization"
    assert c0["extra_meta"] == "kept"   # extra="allow" preserved
    c1 = body["classes"][1]
    assert c1["name"] == "GrowthAnalysis"
    assert c1["kind"] == "analysis"


def test_visualization_classes_in_openapi(client):
    """The /api/visualization-classes route and VisualizationClassesPayload / VizClass
    appear in the generated OpenAPI schema."""
    spec = client.get("/openapi.json").json()
    assert "/api/visualization-classes" in spec["paths"]
    for name in ("VisualizationClassesPayload", "VizClass"):
        assert name in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/composite-resolve
# ---------------------------------------------------------------------------

def test_composite_resolve_missing_returns_null(client):
    """A ref that doesn't match any spec/generator returns 200 with null body
    (empty workspace has no workspace.yaml → resolver returns None immediately)."""
    r = client.get("/api/composite-resolve?ref=missing")
    assert r.status_code == 200
    assert r.json() is None


def test_composite_resolve_typed_passthrough(client, monkeypatch):
    """A valid resolve payload validates through CompositeResolvePayload."""
    import vivarium_dashboard.api.app as _app

    payload = {
        "id": "pbg_ws.composites.my_comp",
        "name": "My Composite",
        "description": "A test composite",
        "parameters": {"n": 10},
        "state": {"store": {}},
        "svg": None,
        "kind": "spec",
        "module": "pbg_ws.composites",
        "default_n_steps": None,
        "extra_field": "preserved",
    }
    monkeypatch.setattr(_app, "resolve_composite", lambda ws, ref: payload)
    r = client.get("/api/composite-resolve?ref=pbg_ws.composites.my_comp")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "pbg_ws.composites.my_comp"
    assert body["name"] == "My Composite"
    assert body["kind"] == "spec"
    assert body["extra_field"] == "preserved"   # extra="allow" works


def test_composite_resolve_in_openapi(client):
    """The /api/composite-resolve route and CompositeResolvePayload appear in
    the OpenAPI schema — proving the typed seam is wired up correctly."""
    spec = client.get("/openapi.json").json()
    assert "/api/composite-resolve" in spec["paths"]
    assert "CompositeResolvePayload" in spec["components"]["schemas"]

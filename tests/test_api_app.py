"""Tests for the FastAPI seam (vivarium_dashboard.api.app)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vivarium_dashboard.api import app as api_app
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace
from vivarium_dashboard.lib import _root


@pytest.fixture(autouse=True)
def _reset_active_workspace():
    """get_workspace() now reads the shared active-workspace root, so reset it
    to None before/after each test to prevent cross-test state leakage."""
    saved = _root.get_workspace_root()
    _root._WS_ROOT = None
    yield
    _root._WS_ROOT = saved


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
    """get_workspace honors the env var and defaults to cwd (no root registered)."""
    monkeypatch.delenv("VIVARIUM_DASHBOARD_WORKSPACE", raising=False)
    assert get_workspace() == Path(".").resolve()
    monkeypatch.setenv("VIVARIUM_DASHBOARD_WORKSPACE", "/tmp/ws-xyz")
    assert get_workspace() == Path("/tmp/ws-xyz").resolve()


def test_workspace_prefers_registered_root(tmp_path, monkeypatch):
    """A root registered via active_workspace wins over the env var."""
    monkeypatch.setenv("VIVARIUM_DASHBOARD_WORKSPACE", "/tmp/ws-env")
    active_workspace.set_workspace_root(tmp_path)
    assert get_workspace() == tmp_path.resolve()
    # And both facade + _root see the SAME value (one _WS_ROOT).
    assert _root.get_workspace_root() == tmp_path.resolve()


def test_dependency_override_still_wins(tmp_path):
    """dependency_overrides[get_workspace] takes precedence over a registered root."""
    active_workspace.set_workspace_root(tmp_path / "registered")
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: tmp_path / "override"
    client = TestClient(app)
    # The override path is empty, so the simulations route returns its empty body.
    resp = client.get("/api/simulations")
    assert resp.status_code == 200


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


# ---------------------------------------------------------------------------
# /api/registry
# ---------------------------------------------------------------------------

def test_registry_empty_workspace(client, monkeypatch):
    """An empty workspace yields the typed empty payload, not a 500.

    We patch ``build_registry`` to return the empty shape that a workspace with
    no importable package would produce (subprocess fails gracefully) so the
    test is deterministic regardless of which pbg packages are installed."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(_app, "build_registry", lambda ws, **kw: {
        "processes": [], "types": [], "imports": [],
    })
    r = client.get("/api/registry")
    assert r.status_code == 200
    body = r.json()
    assert body["processes"] == []
    assert body["types"] == []
    assert body["imports"] == []


def test_registry_typed_passthrough(client, monkeypatch):
    """The route validates the builder's output through RegistryPayload;
    extra top-level fields (default_emitter, workspace_pkgs, error) are
    preserved by extra='allow'."""
    import vivarium_dashboard.api.app as _app

    payload = {
        "processes": [
            {
                "name": "MyProcess",
                "address": "pbg_ws.processes.my_process.MyProcess",
                "kind": "process",
                "schema_preview": "{}",
                "aliases": ["my_process.MyProcess"],
                "source": "in_workspace",
            },
            {
                "name": "ParquetEmitter",
                "address": "pbg_emitters.parquet.ParquetEmitter",
                "kind": "emitter",
                "schema_preview": "",
                "aliases": [],
                "source": "framework",
                "is_workspace_default": True,   # extra field
            },
        ],
        "types": [
            {"name": "float", "schema_preview": "<class 'float'>"},
        ],
        "imports": [
            {"name": "pbg-oxidizeme", "package": "pbg_oxidizeme",
             "source": "https://github.com/x/pbg-oxidizeme",
             "ref": "main", "description": "oxidative stress"},
        ],
        "default_emitter": "parquet",          # top-level extra field
        "workspace_pkgs": ["pbg_ws"],          # top-level extra field
    }
    monkeypatch.setattr(_app, "build_registry", lambda ws, **kw: payload)
    r = client.get("/api/registry")
    assert r.status_code == 200
    body = r.json()

    # Typed fields
    assert len(body["processes"]) == 2
    p0 = body["processes"][0]
    assert p0["name"] == "MyProcess"
    assert p0["kind"] == "process"
    assert p0["source"] == "in_workspace"
    p1 = body["processes"][1]
    assert p1["kind"] == "emitter"
    assert p1["is_workspace_default"] is True     # extra="allow" on RegistryProcess

    assert body["types"][0]["name"] == "float"
    imp = body["imports"][0]
    assert imp["package"] == "pbg_oxidizeme"
    assert imp["description"] == "oxidative stress"

    # Extra top-level keys preserved by RegistryPayload extra="allow"
    assert body["default_emitter"] == "parquet"
    assert body["workspace_pkgs"] == ["pbg_ws"]


def test_registry_error_field_preserved(client, monkeypatch):
    """When the subprocess fails, build_registry returns an 'error' key alongside
    empty lists.  The route must not 422 — RegistryPayload extra='allow' keeps it."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(_app, "build_registry", lambda ws, **kw: {
        "processes": [], "types": [], "imports": [],
        "error": "subprocess failed: ModuleNotFoundError: No module named 'pbg_ws'",
    })
    r = client.get("/api/registry")
    assert r.status_code == 200
    body = r.json()
    assert body["processes"] == []
    assert "error" in body
    assert "ModuleNotFoundError" in body["error"]


def test_registry_in_openapi(client):
    """The /api/registry route and RegistryPayload appear in the OpenAPI schema."""
    spec = client.get("/openapi.json").json()
    assert "/api/registry" in spec["paths"]
    components = spec["components"]["schemas"]
    for name in ("RegistryPayload", "RegistryProcess", "RegistryType", "RegistryImport"):
        assert name in components, f"{name} missing from openapi.json"


# ---------------------------------------------------------------------------
# /api/composites
# ---------------------------------------------------------------------------

def test_composites_typed_passthrough(client, monkeypatch):
    """The route validates composites_via_subprocess output through CompositesPayload.

    Both a spec-kind and a generator-kind composite survive; the generator carries
    an extra field (workspace_local) that is preserved by CompositeRecord extra='allow'.
    """
    import vivarium_dashboard.api.app as _app

    payload = {
        "composites": [
            {
                "id": "pbg_ws.composites.baseline",
                "name": "baseline",
                "kind": "spec",
                "module": "pbg_ws.composites",
            },
            {
                "id": "pbg_ws.composites.growth.growth",
                "name": "growth",
                "kind": "generator",
                "module": "pbg_ws.composites.growth",
                "workspace_local": True,           # extra field — must survive
                "default_n_steps": 100,            # another extra field
            },
        ],
        "workspace_package": "pbg_ws",
    }
    monkeypatch.setattr(_app, "composites_via_subprocess", lambda ws: payload)

    r = client.get("/api/composites")
    assert r.status_code == 200
    body = r.json()

    assert body["workspace_package"] == "pbg_ws"
    assert body["error"] is None
    assert len(body["composites"]) == 2

    spec_rec = body["composites"][0]
    assert spec_rec["id"] == "pbg_ws.composites.baseline"
    assert spec_rec["kind"] == "spec"
    assert spec_rec["module"] == "pbg_ws.composites"

    gen_rec = body["composites"][1]
    assert gen_rec["kind"] == "generator"
    assert gen_rec["workspace_local"] is True         # extra="allow" preserved
    assert gen_rec["default_n_steps"] == 100          # extra="allow" preserved


def test_composites_subprocess_none_returns_error_payload(client, monkeypatch):
    """When composites_via_subprocess returns None the route must not 500 — it
    returns the empty + error payload with HTTP 200."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(_app, "composites_via_subprocess", lambda ws: None)

    r = client.get("/api/composites")
    assert r.status_code == 200
    body = r.json()
    assert body["composites"] == []
    assert body["error"] is not None
    assert "unavailable" in body["error"]


def test_composites_in_openapi(client):
    """The /api/composites route and CompositesPayload / CompositeRecord appear
    in the generated OpenAPI schema."""
    spec = client.get("/openapi.json").json()
    assert "/api/composites" in spec["paths"]
    components = spec["components"]["schemas"]
    for name in ("CompositesPayload", "CompositeRecord"):
        assert name in components, f"{name} missing from openapi.json"


# ---------------------------------------------------------------------------
# /api/investigations
# ---------------------------------------------------------------------------

def test_investigations_empty_workspace(client):
    """An empty workspace yields the typed empty payload (no study dirs), not a 500."""
    r = client.get("/api/investigations")
    assert r.status_code == 200
    assert r.json() == {"investigations": []}


def test_investigations_typed_passthrough(client, monkeypatch):
    """The route validates the builder output through InvestigationsPayload.

    Covers:
    - A rich valid row with many extra keys (extra='allow' preserves them).
    - An invalid row {name, status, error} (the parse-failure shape).
    Both must validate through the model without 422.
    """
    import vivarium_dashboard.api.app as _app

    rich_row = {
        "name": "dnaa-1",
        "status": "ran",
        "phase": "simulate",
        "n_simulations": 3,
        "n_studies": None,
        "description": "DnaA binding study",
        "composite": "pbg_ws.composites.dnaa",
        "composites": [],
        "topic": "replication",
        "tags": ["dnaa", "binding"],
        "last_run": None,
        "baseline_names": ["dnaa-baseline"],
        "n_baseline": 1,
        "n_variants": 2,
        "n_groups": 0,
        "n_interventions": 0,
        "n_behaviors": 3,
        "n_readouts": 5,
        "n_requirements": 1,
        "n_comparisons": 0,
        "n_runs": 3,
        "baseline_source": "pbg_ws:dnaa_baseline",
        "conclusions_excerpt": "The binding affinity...",
        "parent_studies": [],
        "blocked": False,
        "blocked_by": [],
        "extra_future_key": "preserved",   # extra="allow"
    }
    invalid_row = {
        "name": "broken-study",
        "status": "invalid",
        "error": "malformed YAML: ...",
    }
    payload = {"investigations": [rich_row, invalid_row]}

    from unittest.mock import patch
    with patch(
        "vivarium_dashboard.lib.investigations_index.build_investigations",
        return_value=payload,
    ):
        r = client.get("/api/investigations")

    assert r.status_code == 200
    body = r.json()
    assert len(body["investigations"]) == 2

    row0 = body["investigations"][0]
    assert row0["name"] == "dnaa-1"
    assert row0["status"] == "ran"
    assert row0["n_simulations"] == 3
    assert row0["extra_future_key"] == "preserved"   # extra="allow" works

    row1 = body["investigations"][1]
    assert row1["name"] == "broken-study"
    assert row1["status"] == "invalid"
    assert "malformed YAML" in row1["error"]


def test_investigations_in_openapi(client):
    """The /api/investigations route and InvestigationsPayload / InvestigationRow
    appear in the generated OpenAPI schema."""
    spec = client.get("/openapi.json").json()
    assert "/api/investigations" in spec["paths"]
    components = spec["components"]["schemas"]
    for name in ("InvestigationsPayload", "InvestigationRow"):
        assert name in components, f"{name} missing from openapi.json"


# ---------------------------------------------------------------------------
# /api/catalog
# ---------------------------------------------------------------------------

def test_catalog_empty_workspace(client):
    """An empty workspace (no workspace.yaml) returns 200 with an empty or
    error-flagged modules list — never a 500."""
    r = client.get("/api/catalog")
    assert r.status_code == 200
    body = r.json()
    # May have an 'error' key but must have a 'modules' list.
    assert "modules" in body
    assert isinstance(body["modules"], list)


def test_catalog_typed_passthrough(client):
    """The route validates the builder output through CatalogPayload.

    Covers:
    - A rich module row with many extra keys (extra='allow' preserves them).
    - Extra keys on the payload itself (extra='allow' on CatalogPayload).
    Both must validate through the model without 422.
    """
    from unittest.mock import patch

    rich_module = {
        "name": "viva-munk",
        "installed": True,
        "install_source": "pyproject",
        "module": "viva_munk",
        "description": "Particle visualization library.",
        "package": "viva_munk",
        "tags": ["visualization", "particles"],
        "version": "0.4.2",             # extra key — preserved
        "workspace_local": False,        # extra key — preserved
        "future_key": "preserved",       # extra key — preserved
    }
    payload = {"modules": [rich_module], "extra_top_level": "ok"}

    with patch(
        "vivarium_dashboard.api.app.build_catalog",
        return_value=payload,
    ):
        r = client.get("/api/catalog")

    assert r.status_code == 200
    body = r.json()
    assert len(body["modules"]) == 1

    mod = body["modules"][0]
    assert mod["name"] == "viva-munk"
    assert mod["installed"] is True
    assert mod["install_source"] == "pyproject"
    assert mod["description"] == "Particle visualization library."
    assert mod["future_key"] == "preserved"     # extra="allow" works
    assert mod["tags"] == ["visualization", "particles"]


def test_catalog_workspace_yaml(tmp_path):
    """A workspace.yaml without imports yields the workspace's own module (if
    the package dir exists) or an empty modules list."""
    import yaml as _yaml
    from vivarium_dashboard.lib.catalog import build_catalog

    # Minimal workspace.yaml — no package_path, no imports, no pbg_superpowers
    (tmp_path / "workspace.yaml").write_text(
        _yaml.dump({"name": "test-ws", "description": "test"}),
        encoding="utf-8",
    )
    result = build_catalog(tmp_path)
    assert isinstance(result, dict)
    assert "modules" in result
    assert isinstance(result["modules"], list)


def test_catalog_in_openapi(client):
    """The /api/catalog route and CatalogPayload / CatalogModule appear in
    the generated OpenAPI schema."""
    spec = client.get("/openapi.json").json()
    assert "/api/catalog" in spec["paths"]
    components = spec["components"]["schemas"]
    for name in ("CatalogPayload", "CatalogModule"):
        assert name in components, f"{name} missing from openapi.json"


# ---------------------------------------------------------------------------
# /api/git-status
# ---------------------------------------------------------------------------

def test_git_status_empty_workspace(client, monkeypatch):
    """Empty workspace (no git) → 200 with default/null fields, not a 500."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(
        _app._git_status, "build_git_status",
        lambda ws: {
            "upstream_repo": None, "branch": None, "push_state": "no_origin",
            "ahead": 0, "behind": 0, "branch_url": None, "repo_url": None,
            "pr_number": None, "pr_url": None, "base": "main",
            "ahead_of_base": 0, "dirty_count": 0, "compare_url": None,
            "pr_state": None, "gh_available": False, "has_active_workstream": False,
        },
    )
    r = client.get("/api/git-status")
    assert r.status_code == 200
    body = r.json()
    assert body["push_state"] == "no_origin"
    assert body["branch"] is None
    assert body["gh_available"] is False


def test_git_status_with_data(client, monkeypatch):
    """Typed response validates a realistic payload through GitStatus."""
    import vivarium_dashboard.api.app as _app

    payload = {
        "upstream_repo": "org/repo", "branch": "feat/thing", "push_state": "ahead",
        "ahead": 3, "behind": 0, "branch_url": "https://github.com/org/repo/tree/feat/thing",
        "repo_url": "https://github.com/org/repo", "pr_number": 42,
        "pr_url": "https://github.com/org/repo/pull/42", "base": "main",
        "ahead_of_base": 3, "dirty_count": 1, "compare_url": "https://github.com/org/repo/compare/main...feat/thing",
        "pr_state": "OPEN", "gh_available": True, "has_active_workstream": True,
    }
    monkeypatch.setattr(_app._git_status, "build_git_status", lambda ws: payload)
    r = client.get("/api/git-status")
    assert r.status_code == 200
    body = r.json()
    assert body["upstream_repo"] == "org/repo"
    assert body["push_state"] == "ahead"
    assert body["pr_number"] == 42
    assert body["gh_available"] is True


def test_git_status_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/git-status" in spec["paths"]
    assert "GitStatus" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/work-status
# ---------------------------------------------------------------------------

def test_work_status_inactive(client, monkeypatch):
    """No active workstream → EXACTLY {active: false} (byte-identical to legacy).

    The discriminated union must not leak the active model's 13 null defaults.
    """
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(_app._git_status, "build_work_status", lambda ws: {"active": False})
    r = client.get("/api/work-status")
    assert r.status_code == 200
    assert r.json() == {"active": False}     # exactly one key, no null leakage


def test_work_status_active(client, monkeypatch):
    """Active workstream → full 14-key payload, including null pr_number/pr_url."""
    import vivarium_dashboard.api.app as _app

    payload = {
        "active": True, "branch": "feat/my-branch", "base": "main",
        "commits_ahead": 5, "commits_behind": 2, "behind_ref": "origin/main",
        "stale": False, "stale_threshold": 20, "unpushed": 5, "pushed": False,
        "has_origin": True, "gh_available": True, "pr_number": None, "pr_url": None,
    }
    monkeypatch.setattr(_app._git_status, "build_work_status", lambda ws: payload)
    r = client.get("/api/work-status")
    assert r.status_code == 200
    body = r.json()
    assert body == payload                   # byte-identical, all 14 keys incl. nulls
    assert body["active"] is True
    assert body["commits_ahead"] == 5
    assert body["pr_number"] is None         # nullable active-path key NOT dropped


def test_work_status_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/work-status" in spec["paths"]
    schemas = spec["components"]["schemas"]
    assert "WorkStatusActive" in schemas
    assert "WorkStatusInactive" in schemas


# ---------------------------------------------------------------------------
# /api/branch-staleness
# ---------------------------------------------------------------------------

def test_branch_staleness_with_branch(client, monkeypatch):
    """Happy path: explicit ?branch= param."""
    import vivarium_dashboard.api.app as _app

    payload = {
        "branch": "feat/x", "base": "main", "behind_ref": "origin/main",
        "commits_behind": 3, "stale_threshold": 20, "stale": False,
    }
    monkeypatch.setattr(
        _app._git_status, "build_branch_staleness",
        lambda ws, branch, base: payload,
    )
    r = client.get("/api/branch-staleness?branch=feat%2Fx")
    assert r.status_code == 200
    body = r.json()
    assert body["branch"] == "feat/x"
    assert body["commits_behind"] == 3


def test_branch_staleness_400_no_branch(client, monkeypatch):
    """No ?branch= and HEAD can't be resolved → HTTP 400."""
    import vivarium_dashboard.api.app as _app

    from vivarium_dashboard.lib.git_status import NoBranchError

    def _raise(ws, branch, base):
        raise NoBranchError("could not determine current branch + no ?branch= given")

    monkeypatch.setattr(_app._git_status, "build_branch_staleness", _raise)
    r = client.get("/api/branch-staleness")
    assert r.status_code == 400
    # Legacy shape: {"error": <msg>}, not FastAPI's default {"detail": ...}
    assert r.json() == {"error": "could not determine current branch + no ?branch= given"}


def test_branch_staleness_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/branch-staleness" in spec["paths"]
    assert "BranchStaleness" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/dirty-status
# ---------------------------------------------------------------------------

def test_dirty_status_clean(client, monkeypatch):
    """Clean workspace → {count: 0, files: []}."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(
        _app._git_status, "build_dirty_status",
        lambda ws: {"count": 0, "files": []},
    )
    r = client.get("/api/dirty-status")
    assert r.status_code == 200
    body = r.json()
    assert body == {"count": 0, "files": []}


def test_dirty_status_with_files(client, monkeypatch):
    """Dirty workspace → count + files list."""
    import vivarium_dashboard.api.app as _app

    payload = {
        "count": 2,
        "files": [
            {"status": "M", "path": "src/foo.py"},
            {"status": "??", "path": "new_file.txt"},
        ],
    }
    monkeypatch.setattr(_app._git_status, "build_dirty_status", lambda ws: payload)
    r = client.get("/api/dirty-status")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["files"][0]["status"] == "M"
    assert body["files"][1]["path"] == "new_file.txt"


def test_dirty_status_500_git_failure(client, monkeypatch):
    """git status failure → HTTP 500."""
    import subprocess
    import vivarium_dashboard.api.app as _app

    def _raise(ws):
        raise subprocess.CalledProcessError(128, ["git", "status"], stderr="not a git repo")

    monkeypatch.setattr(_app._git_status, "build_dirty_status", _raise)
    r = client.get("/api/dirty-status")
    assert r.status_code == 500
    # Legacy shape: {"error": "git status failed: ..."}, not {"detail": ...}
    body = r.json()
    assert set(body) == {"error"}
    assert body["error"].startswith("git status failed: not a git repo")


def test_dirty_status_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/dirty-status" in spec["paths"]
    for name in ("DirtyStatus", "DirtyFile"):
        assert name in spec["components"]["schemas"], f"{name} missing"


# ---------------------------------------------------------------------------
# /api/branches
# ---------------------------------------------------------------------------

def test_branches_empty(client, monkeypatch):
    """No stage/* branches → empty list, not a 500."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(
        _app._git_status, "list_branches",
        lambda ws: {"branches": [], "current": "main"},
    )
    r = client.get("/api/branches")
    assert r.status_code == 200
    body = r.json()
    assert body["branches"] == []
    assert body["current"] == "main"


def test_branches_with_data(client, monkeypatch):
    """Stage branches are typed through BranchInfo + BranchCommit."""
    import vivarium_dashboard.api.app as _app

    payload = {
        "branches": [
            {
                "name": "stage/feat-a",
                "last_commit": {"sha": "abc1234", "subject": "add feat", "date": "2024-01-01"},
                "ahead_of_main": 2,
            }
        ],
        "current": "main",
    }
    monkeypatch.setattr(_app._git_status, "list_branches", lambda ws: payload)
    r = client.get("/api/branches")
    assert r.status_code == 200
    body = r.json()
    assert len(body["branches"]) == 1
    b = body["branches"][0]
    assert b["name"] == "stage/feat-a"
    assert b["last_commit"]["sha"] == "abc1234"
    assert b["ahead_of_main"] == 2


def test_branches_500_on_git_error(client, monkeypatch):
    """A top-level git failure (builder returns {error}) → HTTP 500, matching
    the legacy _serve_branches — not a swallowed 200."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(
        _app._git_status, "list_branches",
        lambda ws: {"error": "fatal: not a git repository"},
    )
    r = client.get("/api/branches")
    assert r.status_code == 500
    # Legacy shape: {"error": <msg>}, not FastAPI's default {"detail": ...}
    assert r.json() == {"error": "fatal: not a git repository"}


def test_branches_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/branches" in spec["paths"]
    for name in ("BranchesPayload", "BranchInfo", "BranchCommit"):
        assert name in spec["components"]["schemas"], f"{name} missing"


# ---------------------------------------------------------------------------
# /api/branch-diff
# ---------------------------------------------------------------------------

def test_branch_diff_valid(client, monkeypatch):
    """Happy path: ?branch= returns log + diff_stat."""
    import vivarium_dashboard.api.app as _app

    payload = {
        "branch": "stage/feat-a",
        "log": "abc1234 add feat\n",
        "diff_stat": " src/foo.py | 1 +\n 1 file changed\n",
    }
    monkeypatch.setattr(_app._git_status, "build_branch_diff", lambda ws, branch: payload)
    r = client.get("/api/branch-diff?branch=stage%2Ffeat-a")
    assert r.status_code == 200
    body = r.json()
    assert body["branch"] == "stage/feat-a"
    assert "add feat" in body["log"]


def test_branch_diff_400_invalid_branch(client, monkeypatch):
    """Invalid branch name → HTTP 400."""
    import vivarium_dashboard.api.app as _app

    def _raise(ws, branch):
        raise ValueError(f"invalid branch name: {branch!r}")

    monkeypatch.setattr(_app._git_status, "build_branch_diff", _raise)
    r = client.get("/api/branch-diff?branch=../evil")
    assert r.status_code == 400
    # Legacy verbatim body: {"error": "invalid branch name"} — NOT the builder's
    # detailed ValueError text, and NOT FastAPI's default {"detail": ...}.
    assert r.json() == {"error": "invalid branch name"}


def test_branch_diff_400_missing_param(client):
    """Missing ?branch= → HTTP 400 (NOT FastAPI's 422 'field required').

    Hits the real builder on the empty tmp workspace; the empty branch fails
    the builder's name validation and surfaces as 400, matching legacy."""
    r = client.get("/api/branch-diff")
    assert r.status_code == 400
    assert r.json() == {"error": "invalid branch name"}


def test_branch_diff_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/branch-diff" in spec["paths"]
    assert "BranchDiff" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/investigation-viz-html
# ---------------------------------------------------------------------------

def test_investigation_viz_html_200(client, monkeypatch):
    """Happy path: returns typed viz_files list."""
    import vivarium_dashboard.api.app as _app
    monkeypatch.setattr(
        _app._inv_views, "build_investigation_viz_html",
        lambda ws, inv, run_id: {
            "viz_files": [
                {"name": "chart", "html_path": "studies/my-inv/viz/run-1/chart.html"},
                {"name": "summary", "html_path": "studies/my-inv/viz/run-1/summary.html"},
            ]
        },
    )
    r = client.get("/api/investigation-viz-html?investigation=my-inv&run_id=run-1")
    assert r.status_code == 200
    body = r.json()
    assert len(body["viz_files"]) == 2
    assert body["viz_files"][0]["name"] == "chart"
    assert "html_path" in body["viz_files"][0]


def test_investigation_viz_html_400_missing_params(client):
    """Missing ?investigation= or ?run_id= → HTTP 400 with {error, viz_files: []}
    (NOT FastAPI's default {"detail": ...})."""
    r = client.get("/api/investigation-viz-html")
    assert r.status_code == 400
    body = r.json()
    assert set(body) == {"error", "viz_files"}
    assert body["viz_files"] == []


def test_investigation_viz_html_400_body_is_not_detail(client):
    """Error body must use 'error' key, not FastAPI's 'detail'."""
    r = client.get("/api/investigation-viz-html?investigation=x")
    assert r.status_code == 400
    assert "detail" not in r.json()
    assert "error" in r.json()


def test_investigation_viz_html_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/investigation-viz-html" in spec["paths"]
    for name in ("InvestigationVizHtmlPayload", "VizHtmlFile"):
        assert name in spec["components"]["schemas"], f"{name} missing from OpenAPI schema"


# ---------------------------------------------------------------------------
# /api/investigation-composites
# ---------------------------------------------------------------------------

def test_investigation_composites_200(client, monkeypatch):
    """Happy path: returns typed composites list."""
    import vivarium_dashboard.api.app as _app
    monkeypatch.setattr(
        _app._inv_views, "build_investigation_composites",
        lambda ws, inv: {
            "composites": [
                {"name": "baseline-v1", "source": "pbg_ws.composites.baseline", "params": {"n": 10}},
            ]
        },
    )
    r = client.get("/api/investigation-composites?investigation=my-inv")
    assert r.status_code == 200
    body = r.json()
    assert len(body["composites"]) == 1
    c = body["composites"][0]
    assert c["name"] == "baseline-v1"
    assert c["source"] == "pbg_ws.composites.baseline"
    assert c["params"] == {"n": 10}


def test_investigation_composites_400_missing(client):
    """Missing ?investigation= → HTTP 400, {"error": ...} body."""
    r = client.get("/api/investigation-composites")
    assert r.status_code == 400
    assert "error" in r.json()
    assert "detail" not in r.json()


def test_investigation_composites_404_not_found(client, monkeypatch):
    """Unknown investigation → HTTP 404, {"error": ...}."""
    import vivarium_dashboard.api.app as _app
    from vivarium_dashboard.lib.investigation_views import InvViewError

    def _raise(ws, inv):
        raise InvViewError({"error": f"investigation '{inv}' not found"}, 404)

    monkeypatch.setattr(_app._inv_views, "build_investigation_composites", _raise)
    r = client.get("/api/investigation-composites?investigation=missing")
    assert r.status_code == 404
    assert r.json()["error"].startswith("investigation")
    assert "detail" not in r.json()


def test_investigation_composites_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/investigation-composites" in spec["paths"]
    for name in ("InvestigationCompositesPayload", "InvestigationCompositeEntry"):
        assert name in spec["components"]["schemas"], f"{name} missing from OpenAPI schema"


# NOTE: /api/investigation-rigor is intentionally NOT ported in this batch
# (deferred to Batch 3 — it needs the per-study run-merging loader). The route
# stays on the legacy stdlib handler, so there are no FastAPI rigor tests here.


# ---------------------------------------------------------------------------
# /api/investigation-composite-doc
# ---------------------------------------------------------------------------

def test_investigation_composite_doc_200(client, monkeypatch):
    """Happy path: returns {state: <parsed YAML>}."""
    import vivarium_dashboard.api.app as _app
    monkeypatch.setattr(
        _app._inv_views, "build_investigation_composite_doc",
        lambda ws, inv, comp: {"state": {"process": "MyProcess", "config": {"n": 10}}},
    )
    r = client.get("/api/investigation-composite-doc?investigation=my-inv&composite=my-comp")
    assert r.status_code == 200
    body = r.json()
    assert body["state"]["process"] == "MyProcess"
    assert body["state"]["config"]["n"] == 10


def test_investigation_composite_doc_400_missing(client):
    """Missing ?investigation= or ?composite= → HTTP 400."""
    r = client.get("/api/investigation-composite-doc")
    assert r.status_code == 400
    assert "error" in r.json()
    assert "detail" not in r.json()


def test_investigation_composite_doc_404_not_found(client, monkeypatch):
    """Composite file absent → HTTP 404, {"error": "composite document not found"}."""
    import vivarium_dashboard.api.app as _app
    from vivarium_dashboard.lib.investigation_views import InvViewError

    def _raise(ws, inv, comp):
        raise InvViewError({"error": "composite document not found"}, 404)

    monkeypatch.setattr(_app._inv_views, "build_investigation_composite_doc", _raise)
    r = client.get("/api/investigation-composite-doc?investigation=x&composite=y")
    assert r.status_code == 404
    assert r.json() == {"error": "composite document not found"}


def test_investigation_composite_doc_500_parse_failure(client, monkeypatch):
    """YAML parse failure → HTTP 500, {"error": "parse failed: ..."}."""
    import vivarium_dashboard.api.app as _app
    from vivarium_dashboard.lib.investigation_views import InvViewError

    def _raise(ws, inv, comp):
        raise InvViewError({"error": "parse failed: unexpected char"}, 500)

    monkeypatch.setattr(_app._inv_views, "build_investigation_composite_doc", _raise)
    r = client.get("/api/investigation-composite-doc?investigation=x&composite=y")
    assert r.status_code == 500
    assert "parse failed" in r.json()["error"]
    assert "detail" not in r.json()


def test_investigation_composite_doc_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/investigation-composite-doc" in spec["paths"]
    assert "InvestigationCompositeDocPayload" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/investigation-hypotheses
# ---------------------------------------------------------------------------

def test_investigation_hypotheses_200(client, monkeypatch):
    """Happy path: returns {hypotheses: [...], investigation: name}."""
    import vivarium_dashboard.api.app as _app
    monkeypatch.setattr(
        _app._inv_views, "build_investigation_hypotheses",
        lambda ws, name: {
            "hypotheses": [
                {"id": "H1", "statement": "X causes Y", "support_log": []},
            ],
            "investigation": "my-inv",
        },
    )
    r = client.get("/api/investigation-hypotheses?investigation=my-inv")
    assert r.status_code == 200
    body = r.json()
    assert body["investigation"] == "my-inv"
    assert len(body["hypotheses"]) == 1
    assert body["hypotheses"][0]["id"] == "H1"


def test_investigation_hypotheses_missing_returns_empty(client):
    """Missing or unknown investigation → 200 with empty hypotheses (never 404)."""
    r = client.get("/api/investigation-hypotheses")
    assert r.status_code == 200
    body = r.json()
    assert "hypotheses" in body
    assert body["hypotheses"] == []


def test_investigation_hypotheses_query_param_aliases(client, monkeypatch):
    """The slug accepts ?investigation= / ?inv= / ?name= (legacy precedence:
    investigation > inv > name), matching the stdlib dispatcher."""
    import vivarium_dashboard.api.app as _app
    seen = {}

    def _capture(ws, name):
        seen["slug"] = name
        return {"hypotheses": [], "investigation": name}

    monkeypatch.setattr(_app._inv_views, "build_investigation_hypotheses", _capture)

    # ?inv= alias resolves when ?investigation= is absent
    client.get("/api/investigation-hypotheses?inv=via-inv")
    assert seen["slug"] == "via-inv"

    # ?name= alias resolves when both ?investigation= and ?inv= are absent
    client.get("/api/investigation-hypotheses?name=via-name")
    assert seen["slug"] == "via-name"

    # precedence: investigation wins over inv and name
    client.get("/api/investigation-hypotheses?investigation=win&inv=lose&name=lose2")
    assert seen["slug"] == "win"


def test_investigation_hypotheses_extra_fields_preserved(client, monkeypatch):
    """Extra fields on hypothesis entries survive extra='allow'."""
    import vivarium_dashboard.api.app as _app
    monkeypatch.setattr(
        _app._inv_views, "build_investigation_hypotheses",
        lambda ws, name: {
            "hypotheses": [{"id": "H1", "statement": "S", "custom_field": "kept"}],
            "investigation": "x",
            "extra_top": "also_kept",
        },
    )
    r = client.get("/api/investigation-hypotheses?investigation=x")
    assert r.status_code == 200
    body = r.json()
    assert body["extra_top"] == "also_kept"   # extra="allow" on payload
    # hypothesis entries are list[Any] so arbitrary keys survive
    assert body["hypotheses"][0]["custom_field"] == "kept"


def test_investigation_hypotheses_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/investigation-hypotheses" in spec["paths"]
    assert "InvestigationHypothesesPayload" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/study-rigor
# ---------------------------------------------------------------------------

def test_study_rigor_200(client, monkeypatch):
    """Happy path: the rigor scorecard passes through untouched (extra='allow')."""
    import vivarium_dashboard.api.app as _app
    payload = {
        "study_type": "perturbation", "mode": "hypothesis", "descriptive": False,
        "dimensions": [{"id": "replication", "severity": "ok"}],
        "score": {"gap": 0, "warn": 0, "ok": 1, "na": 0, "total": 1},
        "summary": "1/1 rigor dimensions addressed",
    }
    monkeypatch.setattr(_app._rigor_views, "build_study_rigor", lambda ws, slug: payload)
    r = client.get("/api/study-rigor?study=my-study")
    assert r.status_code == 200
    assert r.json() == payload   # nothing stripped or injected


def test_study_rigor_400_missing(client):
    """Missing ?study= → HTTP 400, {"error": ...} (not {"detail": ...})."""
    r = client.get("/api/study-rigor")
    assert r.status_code == 400
    assert r.json() == {"error": "missing ?study="}
    assert "detail" not in r.json()


def test_study_rigor_404_not_found(client):
    """Unknown study → HTTP 404, {"error": "study not found"}."""
    r = client.get("/api/study-rigor?study=nope")
    assert r.status_code == 404
    assert r.json() == {"error": "study not found"}


def test_study_rigor_investigation_alias(client, monkeypatch):
    """Legacy ?investigation= alias selects the study when ?study= is absent."""
    import vivarium_dashboard.api.app as _app
    seen = {}

    def _capture(ws, slug):
        seen["slug"] = slug
        return {"dimensions": [], "score": {}, "summary": ""}

    monkeypatch.setattr(_app._rigor_views, "build_study_rigor", _capture)
    client.get("/api/study-rigor?investigation=via-alias")
    assert seen["slug"] == "via-alias"


def test_study_rigor_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/study-rigor" in spec["paths"]
    assert "StudyRigor" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/investigation-rigor
# ---------------------------------------------------------------------------

def test_investigation_rigor_200(client, monkeypatch):
    """Happy path: the roll-up passes through untouched (extra='allow')."""
    import vivarium_dashboard.api.app as _app
    payload = {
        "dimensions": [{"id": "adversarial", "severity": "warn"}],
        "per_study": {"my-study": {"summary": "ok"}},
        "score": {"gap": 0, "warn": 1, "ok": 0, "na": 0, "total": 1},
        "summary": "1 investigation dimension(s)",
    }
    monkeypatch.setattr(
        _app._rigor_views, "build_investigation_rigor", lambda ws, slug: payload)
    r = client.get("/api/investigation-rigor?investigation=my-inv")
    assert r.status_code == 200
    assert r.json() == payload


def test_investigation_rigor_400_missing(client):
    r = client.get("/api/investigation-rigor")
    assert r.status_code == 400
    assert r.json() == {"error": "missing ?investigation="}
    assert "detail" not in r.json()


def test_investigation_rigor_404_not_found(client):
    r = client.get("/api/investigation-rigor?investigation=nope")
    assert r.status_code == 404
    assert r.json() == {"error": "investigation not found"}


def test_investigation_rigor_200_with_error(client, monkeypatch):
    """An unreadable investigation.yaml degrades to a 200 body carrying error."""
    import vivarium_dashboard.api.app as _app
    monkeypatch.setattr(
        _app._rigor_views, "build_investigation_rigor",
        lambda ws, slug: {"error": "unreadable investigation.yaml: bad"})
    r = client.get("/api/investigation-rigor?investigation=x")
    assert r.status_code == 200
    assert r.json() == {"error": "unreadable investigation.yaml: bad"}


def test_investigation_rigor_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/investigation-rigor" in spec["paths"]
    assert "InvestigationRigor" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/study/{slug}
# ---------------------------------------------------------------------------

import sqlite3  # noqa: E402
import yaml as _yaml  # noqa: E402


def _make_study_workspace(tmp_path):
    """Workspace with a 'my-study' study and a run in runs.db."""
    study_dir = tmp_path / "studies" / "my-study"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        _yaml.dump({
            "name": "my-study",
            "composite": "pbg_ws.composites.baseline",
            "runs": [],
            "simulation_set": [
                {"name": "baseline", "is_baseline": True, "status": "ready"},
            ],
        }),
        encoding="utf-8",
    )
    conn = sqlite3.connect(str(study_dir / "runs.db"))
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT, spec_id TEXT, label TEXT, "
        "params_json TEXT, started_at REAL, completed_at REAL, n_steps INTEGER, "
        "status TEXT, sim_name TEXT, generation_id TEXT)"
    )
    conn.execute(
        "INSERT INTO runs_meta VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("r1", "my-study", "Run", "{}", 1700000000.0, 1700000010.0,
         100, "completed", "baseline", None),
    )
    conn.commit()
    conn.close()
    return tmp_path


def test_study_detail_200(tmp_path):
    """A study with a runs.db → 200, spec has run_id in 'runs'."""
    ws = _make_study_workspace(tmp_path)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    c = TestClient(app)
    r = c.get("/api/study/my-study")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "my-study"
    run_ids = {(rr or {}).get("run_id") for rr in body.get("runs", [])}
    assert "r1" in run_ids


def test_study_detail_404_unknown(client):
    """Unknown study slug → 404, {"error": "study not found: <slug>"}."""
    r = client.get("/api/study/no-such-study")
    assert r.status_code == 404
    body = r.json()
    assert body == {"error": "study not found: no-such-study"}
    assert "detail" not in body


def test_study_detail_400_invalid_slug(client):
    """Invalid slug → 400, {"error": "invalid slug"}.

    FastAPI routes the request first, so only slugs that arrive at the handler
    can be tested here.  Path-traversal slugs that contain an encoded '/'
    (%2F) cause Starlette to normalize the URL before routing, which yields 404
    (the path ceases to match the route pattern) rather than reaching the slug
    validation.  We test slugs that ARE routed but fail the SLUG_RE check:
    uppercase letters, leading/trailing hyphens, underscores at boundaries, etc.
    """
    # These slugs reach the handler but fail SLUG_RE → 400.
    for bad in ("UPPER", "-leading-hyphen", "trailing-"):
        r = client.get(f"/api/study/{bad}")
        assert r.status_code == 400, f"expected 400 for {bad!r}, got {r.status_code}"
        assert r.json() == {"error": "invalid slug"}
        assert "detail" not in r.json()


def test_study_detail_in_openapi(client):
    """The /api/study/{slug} route and StudyDetail appear in the OpenAPI schema."""
    spec = client.get("/openapi.json").json()
    assert "/api/study/{slug}" in spec["paths"]
    assert "StudyDetail" in spec["components"]["schemas"]


def test_study_detail_extra_keys_preserved(tmp_path):
    """StudyDetail uses extra='allow' — the loader's extra keys survive the typed response."""
    ws = _make_study_workspace(tmp_path)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    c = TestClient(app)
    r = c.get("/api/study/my-study")
    assert r.status_code == 200
    body = r.json()
    # simulation_set is one such "extra" key not declared on StudyDetail
    assert "simulation_set" in body
    assert isinstance(body["simulation_set"], list)


def test_study_detail_500_loader_exception(client, monkeypatch):
    """Loader raising → 500 with error + traceback fields."""
    monkeypatch.setattr(
        api_app._study_spec, "load_study_detail_spec",
        lambda ws, slug: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    r = client.get("/api/study/any-study")
    assert r.status_code == 500
    body = r.json()
    assert "error" in body and "traceback" in body
    assert "RuntimeError" in body["error"] and "boom" in body["error"]
    assert "detail" not in body


# ---------------------------------------------------------------------------
# /api/explorer/*  — Data explorer routes
# ---------------------------------------------------------------------------
# Fixture helpers (inline — tests/ has no __init__.py, cross-module import risks)

import json as _json_mod  # noqa: E402


def _make_explorer_db(db_path: Path, n: int = 5, run_id: str = "run-1") -> None:
    """Write a minimal SQLiteEmitter-shaped runs.db with one run."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE simulations (
            simulation_id TEXT PRIMARY KEY, name TEXT,
            started_at TEXT, completed_at TEXT, elapsed_seconds REAL
        );
        CREATE TABLE history (
            simulation_id TEXT, step INTEGER, global_time REAL, state TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO simulations VALUES (?,?,?,?,?)",
        (run_id, "baseline", "2026-01-01T00:00:00", "2026-01-01T00:01:00", 60.0),
    )
    for step in range(n):
        state = {
            "agents": {"0": {
                "listeners": {
                    "mass": {"cell_mass": 100.0 + step},
                    "fba_results": {
                        "base_reaction_fluxes": [1.0 + step, 2.0 + step, 3.0 + step]
                    },
                },
                "bulk": [["GLC", 10 + step], ["ATP", 20 + step]],
            }},
        }
        conn.execute(
            "INSERT INTO history VALUES (?,?,?,?)",
            (run_id, step, float(step), _json_mod.dumps(state)),
        )
    conn.commit()
    conn.close()


def _make_explorer_workspace(tmp_path: Path) -> Path:
    """Workspace with one study + non-empty runs.db."""
    study_dir = tmp_path / "studies" / "demo"
    study_dir.mkdir(parents=True)
    _make_explorer_db(study_dir / "runs.db")
    return tmp_path


# ---------------------------------------------------------------------------
# GET /api/explorer/runs
# ---------------------------------------------------------------------------

def test_explorer_runs_empty_workspace(client):
    """Empty workspace → 200, runs=[]."""
    r = client.get("/api/explorer/runs")
    assert r.status_code == 200
    body = r.json()
    assert "runs" in body
    assert body["runs"] == []


def test_explorer_runs_never_500_on_error(client, monkeypatch):
    """Route exception → 200 with {error, runs: []}, not HTTP 500."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(_app._explorer_data, "list_runs", lambda ws: (_ for _ in ()).throw(
        RuntimeError("simulations-index exploded")
    ))
    r = client.get("/api/explorer/runs")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body
    assert body["runs"] == []


def test_explorer_runs_happy_path(tmp_path):
    """Fixture workspace → 200, runs list contains the inserted run."""
    ws = _make_explorer_workspace(tmp_path)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    c = TestClient(app)
    r = c.get("/api/explorer/runs")
    assert r.status_code == 200
    body = r.json()
    assert len(body["runs"]) >= 1
    run_ids = {run["run_id"] for run in body["runs"]}
    assert "run-1" in run_ids


def test_explorer_runs_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/explorer/runs" in spec["paths"]
    assert "ExplorerRuns" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# GET /api/explorer/observables
# ---------------------------------------------------------------------------

def test_explorer_observables_missing_db(client):
    """Missing ?db= → 200, {error: 'missing db', categories: {}}."""
    r = client.get("/api/explorer/observables")
    assert r.status_code == 200
    body = r.json()
    assert body["error"] == "missing db"
    assert body["categories"] == {}


def test_explorer_observables_bad_db_returns_200(client):
    """Bad db path that can't be resolved → 200, categories={} (never 500)."""
    r = client.get("/api/explorer/observables?db=/nonexistent/totally/fake.db")
    assert r.status_code == 200
    body = r.json()
    # Either an error key or an empty categories dict — but always 200.
    assert "categories" in body


def test_explorer_observables_happy_path(tmp_path):
    """Fixture workspace → 200, non-empty categories with expected leaves."""
    ws = _make_explorer_workspace(tmp_path)
    db_path = str(ws / "studies" / "demo" / "runs.db")
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    c = TestClient(app)
    r = c.get(f"/api/explorer/observables?db={db_path}")
    assert r.status_code == 200
    body = r.json()
    assert "categories" in body
    cats = body["categories"]
    assert cats, "categories must be non-empty"
    all_paths = [o["path"] for g in cats.values() for o in g]
    assert any("cell_mass" in p for p in all_paths)


def test_explorer_observables_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/explorer/observables" in spec["paths"]
    assert "ExplorerObservables" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# GET /api/explorer/series
# ---------------------------------------------------------------------------

def test_explorer_series_missing_db(client):
    """Missing ?db= → 200, {error: 'missing db', time: [], series: {}}."""
    r = client.get("/api/explorer/series")
    assert r.status_code == 200
    body = r.json()
    assert body["error"] == "missing db"
    assert body["time"] == []
    assert body["series"] == {}


def test_explorer_series_subsample_fallback(tmp_path):
    """Non-integer ?subsample= falls back to 400 (not a 422 or 500)."""
    ws = _make_explorer_workspace(tmp_path)
    db_path = str(ws / "studies" / "demo" / "runs.db")
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    c = TestClient(app)
    # Non-integer subsample must not 422; it silently falls back to 400.
    r = c.get(f"/api/explorer/series?db={db_path}&paths=listeners.mass.cell_mass"
              f"&subsample=NOT_AN_INT")
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body or body.get("time") is not None


def test_explorer_series_hash_index_parsing(tmp_path):
    """'path#index' spec is parsed correctly — vector key uses #index suffix."""
    ws = _make_explorer_workspace(tmp_path)
    db_path = str(ws / "studies" / "demo" / "runs.db")
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    c = TestClient(app)
    r = c.get(
        f"/api/explorer/series?db={db_path}"
        f"&paths=listeners.fba_results.base_reaction_fluxes%230"  # %23 = #
    )
    assert r.status_code == 200
    body = r.json()
    assert "series" in body
    # Key must be "path#0", not "path" (vector index appended)
    assert "listeners.fba_results.base_reaction_fluxes#0" in body["series"]


def test_explorer_series_never_500(client, monkeypatch):
    """Builder exception → 200 with error body."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(
        _app._explorer_data, "get_series",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("zarr exploded")),
    )
    r = client.get("/api/explorer/series?db=/any")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body
    assert body["time"] == [] and body["series"] == {}


def test_explorer_series_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/explorer/series" in spec["paths"]
    assert "ExplorerSeries" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# GET /api/explorer/flux
# ---------------------------------------------------------------------------

def test_explorer_flux_missing_db(client):
    """Missing ?db= → 200, {error: 'missing db', fluxes: {}}."""
    r = client.get("/api/explorer/flux")
    assert r.status_code == 200
    body = r.json()
    assert body["error"] == "missing db"
    assert body["fluxes"] == {}


def test_explorer_flux_step_fallback(client):
    """Non-integer ?step= falls back to 0 (not 422)."""
    # Bad db will fail in the builder but step parsing must not 422.
    r = client.get("/api/explorer/flux?db=/bad&step=NOT_INT")
    assert r.status_code == 200
    body = r.json()
    # Either error or fluxes present — but always 200.
    assert "fluxes" in body or "error" in body


def test_explorer_flux_never_500(client, monkeypatch):
    """Builder exception → 200 with error body."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(
        _app._explorer_data, "get_flux_auto",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("flux exploded")),
    )
    r = client.get("/api/explorer/flux?db=/any")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body
    assert body["fluxes"] == {}


def test_explorer_flux_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/explorer/flux" in spec["paths"]
    assert "ExplorerFlux" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# GET /api/explorer/vector
# ---------------------------------------------------------------------------

def test_explorer_vector_missing_db_or_path(client):
    """Missing ?db= or ?path= → 200 with missing-param error shape."""
    # Both missing
    r = client.get("/api/explorer/vector")
    assert r.status_code == 200
    body = r.json()
    assert body["error"] == "missing db/path"
    assert body["ids"] == [] and body["values"] == []
    assert body["step"] == 0 and body["time"] is None

    # db present but path missing
    r2 = client.get("/api/explorer/vector?db=/some.db")
    assert r2.status_code == 200
    assert r2.json()["error"] == "missing db/path"

    # path present but db missing
    r3 = client.get("/api/explorer/vector?path=listeners.mass.cell_mass")
    assert r3.status_code == 200
    assert r3.json()["error"] == "missing db/path"


def test_explorer_vector_step_fallback(client):
    """Non-integer ?step= falls back to 0 — not a 422."""
    r = client.get("/api/explorer/vector?db=/bad&path=x&step=BAD")
    assert r.status_code == 200
    body = r.json()
    assert "ids" in body and "values" in body


def test_explorer_vector_never_500(client, monkeypatch):
    """Builder exception → 200 with error body."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(
        _app._explorer_data, "get_vector",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("vector exploded")),
    )
    r = client.get("/api/explorer/vector?db=/any&path=x")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body
    assert body["ids"] == [] and body["values"] == []


def test_explorer_vector_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/explorer/vector" in spec["paths"]
    assert "ExplorerVector" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# GET /api/explorer/protein-breakdown
# ---------------------------------------------------------------------------

def test_explorer_protein_breakdown_missing_params(client):
    """Missing ?db= or ?path= → 200 with missing-param error shape."""
    r = client.get("/api/explorer/protein-breakdown")
    assert r.status_code == 200
    body = r.json()
    assert body["error"] == "missing db/path"
    assert body["breakdown"] == {} and body["step"] == 0 and body["time"] is None


def test_explorer_protein_breakdown_never_500(client, monkeypatch):
    """Builder exception → 200 with error body."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(
        _app._explorer_data, "get_protein_breakdown",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("breakdown exploded")),
    )
    r = client.get("/api/explorer/protein-breakdown?db=/any&path=x")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body
    assert body["breakdown"] == {}


def test_explorer_protein_breakdown_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/explorer/protein-breakdown" in spec["paths"]
    assert "ExplorerProteinBreakdown" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# Parity: FastAPI route body == REAL legacy stdlib Handler body
# ---------------------------------------------------------------------------

class TestExplorerServerShimParity:
    """The FastAPI explorer route bodies match the REAL legacy stdlib handler.

    Both servers back onto the same ``lib.explorer_data`` builders, but the
    legacy *handler* also does its own query-string parsing and dict-wrapping
    (e.g. ``{"runs": ...}``).  Comparing against the lib function alone can't
    catch a divergence in that handler-level wrapping, so these tests invoke the
    actual ``server.Handler._get_explorer_*`` methods — constructed via
    ``__new__`` to bypass the socket-bound ``__init__``, with ``_json`` patched
    to capture ``(body, status)`` and ``self.path`` set to the request URL the
    handler parses — and assert the captured body == the FastAPI route body
    (and status 200).  This mirrors ``TestStudyDetailServerShimParity`` and the
    git/rigor/investigation-view ``TestServerShimParity`` classes.
    """

    @staticmethod
    def _invoke_legacy(monkeypatch, ws_root: Path, method_name: str, path: str):
        """Call the real stdlib explorer handler, capturing (body, status)."""
        import vivarium_dashboard.server as server

        monkeypatch.setattr(server, "WORKSPACE", ws_root)
        handler = server.Handler.__new__(server.Handler)
        captured: dict = {}

        def _fake_json(data, code):
            captured["body"] = data
            captured["status"] = code

        handler._json = _fake_json          # type: ignore[method-assign]
        handler.path = path
        getattr(handler, method_name)()
        return captured

    @staticmethod
    def _fastapi_body(ws_root: Path, path: str):
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws_root
        c = TestClient(app)
        return c.get(path)

    def test_runs_parity(self, tmp_path, monkeypatch):
        """GET /api/explorer/runs: real handler body == FastAPI route body."""
        ws = _make_explorer_workspace(tmp_path)
        path = "/api/explorer/runs"

        legacy = self._invoke_legacy(monkeypatch, ws, "_get_explorer_runs", path)
        assert legacy["status"] == 200
        # Non-trivial: the handler wraps list_runs() under a "runs" key.
        assert "runs" in legacy["body"] and legacy["body"]["runs"]

        r = self._fastapi_body(ws, path)
        assert r.status_code == 200
        assert r.json() == legacy["body"]

    def test_observables_parity(self, tmp_path, monkeypatch):
        """GET /api/explorer/observables: real handler body == FastAPI route body."""
        ws = _make_explorer_workspace(tmp_path)
        db_path = str(ws / "studies" / "demo" / "runs.db")
        path = f"/api/explorer/observables?db={db_path}&run=run-1"

        legacy = self._invoke_legacy(
            monkeypatch, ws, "_get_explorer_observables", path)
        assert legacy["status"] == 200
        # Non-trivial: real categorized observables, not an empty/error body.
        assert legacy["body"].get("categories")

        r = self._fastapi_body(ws, path)
        assert r.status_code == 200
        assert r.json() == legacy["body"]

    def test_series_parity(self, tmp_path, monkeypatch):
        """GET /api/explorer/series: real handler body == FastAPI route body."""
        ws = _make_explorer_workspace(tmp_path)
        db_path = str(ws / "studies" / "demo" / "runs.db")
        path = (f"/api/explorer/series?db={db_path}"
                f"&paths=listeners.mass.cell_mass&run=run-1")

        legacy = self._invoke_legacy(
            monkeypatch, ws, "_get_explorer_series", path)
        assert legacy["status"] == 200
        # Non-trivial: a real time axis + named series, not the empty error body.
        assert legacy["body"]["time"]
        assert "listeners.mass.cell_mass" in legacy["body"]["series"]

        r = self._fastapi_body(ws, path)
        assert r.status_code == 200
        assert r.json() == legacy["body"]


class TestStudyDetailServerShimParity:
    """The FastAPI route body matches the legacy server builder for 200 + 404 + 400."""

    def test_200_parity(self, tmp_path, monkeypatch):
        ws = _make_study_workspace(tmp_path)
        import vivarium_dashboard.server as srv
        monkeypatch.setattr(srv, "WORKSPACE", ws)
        srv._WP_CACHE.clear()

        # Legacy builder response
        legacy_bytes, legacy_status = srv.Handler._build_api_study_response("my-study")
        import json as _json
        legacy_body = _json.loads(legacy_bytes)

        # FastAPI route response
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        from fastapi.testclient import TestClient as _TC
        c = _TC(app)
        r = c.get("/api/study/my-study")

        assert r.status_code == legacy_status == 200
        assert r.json() == legacy_body

    def test_404_parity(self, client, tmp_path, monkeypatch):
        import vivarium_dashboard.server as srv
        monkeypatch.setattr(srv, "WORKSPACE", tmp_path)
        srv._WP_CACHE.clear()

        legacy_bytes, legacy_status = srv.Handler._build_api_study_response("no-study")
        import json as _json
        legacy_body = _json.loads(legacy_bytes)

        r = client.get("/api/study/no-study")
        assert r.status_code == legacy_status == 404
        assert r.json() == legacy_body

    def test_400_parity(self, client):
        """Slug that fails SLUG_RE → identical 400 body from both paths."""
        import vivarium_dashboard.server as srv
        import json as _json
        # Use a slug that FastAPI routes (no encoded '/') but SLUG_RE rejects
        bad_slug = "UPPER-CASE"
        legacy_bytes, legacy_status = srv.Handler._build_api_study_response(bad_slug)
        legacy_body = _json.loads(legacy_bytes)

        r = client.get(f"/api/study/{bad_slug}")
        assert r.status_code == legacy_status == 400
        assert r.json() == legacy_body


# ---------------------------------------------------------------------------
# /api/report-lint
# ---------------------------------------------------------------------------

def test_report_lint_200_empty_workspace(client):
    """An empty workspace returns 200 with findings list (possibly empty)."""
    r = client.get("/api/report-lint")
    assert r.status_code == 200
    body = r.json()
    assert "findings" in body
    assert isinstance(body["findings"], list)


def test_report_lint_typed_passthrough(client, monkeypatch):
    """Extra fields on findings survive the typed response (pass-through model)."""
    import vivarium_dashboard.api.app as _app

    monkeypatch.setattr(
        _app._report_views, "build_report_lint",
        lambda ws: (
            {"findings": [
                {"study": "s1", "check": "missing_readouts",
                 "severity": "warning", "message": "no readouts",
                 "field_path": "readouts", "extra_field": "kept"},
            ]},
            200,
        ),
    )
    body = client.get("/api/report-lint").json()
    assert body["findings"][0]["study"] == "s1"
    assert body["findings"][0]["extra_field"] == "kept"  # pass-through preserved


def test_report_lint_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/report-lint" in spec["paths"]
    assert "ReportLint" in spec["components"]["schemas"]


# NOTE: GET /api/linkage-index is intentionally NOT ported in this batch (see
# api/app.py).  Its server worker is exercised by tests/test_linkage_index_endpoint.py
# and the lib builder by tests/test_report_views_lib.py.  Route + LinkageIndex
# model re-added in a later observables/composite-state batch.


# ---------------------------------------------------------------------------
# /api/needs-attention
# ---------------------------------------------------------------------------

def test_needs_attention_200_empty_workspace(client):
    r = client.get("/api/needs-attention")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert isinstance(body["items"], list)


def test_needs_attention_investigation_param(client, monkeypatch):
    """?investigation= param is forwarded to build_needs_attention."""
    import vivarium_dashboard.api.app as _app
    captured: dict = {}

    def _spy(ws, *, investigation=None):
        captured["investigation"] = investigation
        return {"investigation": investigation, "items": [], "summary": {}}, 200

    monkeypatch.setattr(_app._report_views, "build_needs_attention", _spy)
    client.get("/api/needs-attention?investigation=my-inv")
    assert captured["investigation"] == "my-inv"


def test_needs_attention_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/needs-attention" in spec["paths"]
    assert "NeedsAttention" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/inputs
# ---------------------------------------------------------------------------

def test_inputs_200_empty_workspace(client):
    r = client.get("/api/inputs")
    assert r.status_code == 200
    body = r.json()
    assert "investigation" in body
    assert "global" in body
    assert "current" in body


def test_inputs_investigation_param(client, monkeypatch):
    """?investigation= slug is forwarded to lib.report_views.build_inputs."""
    import vivarium_dashboard.api.app as _app
    captured: list = []

    def _spy(ws, slug=None):
        captured.append(slug)
        return {"investigation": {}, "global": {}, "current": slug}

    monkeypatch.setattr(_app._report_views, "build_inputs", _spy)
    client.get("/api/inputs?investigation=my-slug")
    assert captured and captured[0] == "my-slug"


def test_inputs_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/inputs" in spec["paths"]
    assert "InputsPayload" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/iset/{slug}
# ---------------------------------------------------------------------------

def _make_iset_workspace(tmp_path):
    """Workspace with one investigation.yaml."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: ws\n")
    inv = ws / "investigations" / "my-inv"
    inv.mkdir(parents=True)
    import yaml as _yaml
    (inv / "investigation.yaml").write_text(_yaml.safe_dump({
        "name": "my-inv",
        "title": "My Investigation",
        "description": "test",
        "status": "planning",
        "studies": [],
    }))
    return ws


def test_iset_detail_200(tmp_path):
    ws = _make_iset_workspace(tmp_path)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    from fastapi.testclient import TestClient as _TC
    c = _TC(app)
    r = c.get("/api/iset/my-inv")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "my-inv"
    assert body["title"] == "My Investigation"
    assert isinstance(body["studies"], list)


def test_iset_detail_404_unknown_slug(client):
    """Unknown slug returns 404 with the legacy error body."""
    r = client.get("/api/iset/no-such-investigation")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
    assert "no investigation.yaml" in body["error"]
    assert "detail" not in body   # must NOT be FastAPI default {"detail":...}


def test_iset_detail_404_body_matches_legacy(tmp_path, monkeypatch):
    """Exact 404 body parity with the legacy _get_iset_detail handler.

    The legacy handler maps ``_iset_detail_data(...) is None`` to the verbatim
    body ``{"error": "no investigation.yaml for '<slug>'"}``; the FastAPI route
    must emit the identical 404 body.
    """
    import vivarium_dashboard.server as srv

    ws = _make_iset_workspace(tmp_path)
    slug = "no-such"

    # Legacy handler maps a None builder result to the verbatim 404 string.
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    srv._WP_CACHE.clear()
    assert srv.Handler._iset_detail_data(slug) is None
    legacy_body = {"error": f"no investigation.yaml for {slug!r}"}

    # FastAPI route 404 body must be byte-identical.
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    from fastapi.testclient import TestClient as _TC
    c = _TC(app)
    r = c.get(f"/api/iset/{slug}")
    assert r.status_code == 404
    assert r.json() == legacy_body


def test_iset_detail_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/iset/{slug}" in spec["paths"]
    assert "IsetDetail" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/observables, /api/study-observable-check, /api/linkage-index (Batch 8)
# ---------------------------------------------------------------------------

import shutil as _shutil

_OBS_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
_OBS_REF = "pbg_ws_increase_demo.composites.increase-demo"


def _obs_demo_client(tmp_path):
    """A TestClient over a throwaway copy of the real increase-demo workspace."""
    import yaml as _yaml
    ws = tmp_path / "ws"
    _shutil.copytree(_OBS_FIXTURE, ws)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app), ws, _yaml


def test_observables_no_ref_400(client):
    r = client.get("/api/observables")
    assert r.status_code == 400
    assert r.json() == {"error": "ref required"}


def test_observables_unknown_ref_404(tmp_path):
    c, _ws, _yaml = _obs_demo_client(tmp_path)
    r = c.get("/api/observables", params={"ref": "nope.not.a.composite"})
    # Unknown ref → 404 (or 501 if the validator is absent — match legacy).
    assert r.status_code in (404, 501)
    assert "error" in r.json()


def test_observables_real_build_200(tmp_path):
    from vivarium_dashboard.lib import observables_views as _ov
    _ov.clear_cache()
    c, _ws, _yaml = _obs_demo_client(tmp_path)
    r = c.get("/api/observables", params={"ref": _OBS_REF})
    if r.status_code == 501:
        pytest.skip("readout_validation unavailable in this interpreter")
    assert r.status_code == 200
    body = r.json()
    assert "stores.level" in body["leaves"]
    assert body["ref"] == _OBS_REF


def test_study_observable_check_invalid_slug_400(client):
    r = client.get("/api/study-observable-check", params={"study": "UPPER-CASE"})
    assert r.status_code == 400
    assert r.json() == {"error": "invalid slug"}


def test_study_observable_check_not_found_404(client):
    r = client.get("/api/study-observable-check", params={"study": "no-such-study"})
    assert r.status_code == 404
    assert "error" in r.json()


def test_study_observable_check_real_build_200(tmp_path):
    c, ws, _yaml = _obs_demo_client(tmp_path)
    sdir = ws / "studies" / "the-study"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "study.yaml").write_text(_yaml.safe_dump({
        "name": "the-study",
        "baseline": [{"name": "base", "composite": _OBS_REF}],
        "readouts": [
            {"name": "real-one", "store_path": "stores.level"},
            {"name": "phantom-one", "store_path": "stores.nonexistent"},
        ],
    }), encoding="utf-8")
    r = c.get("/api/study-observable-check", params={"study": "the-study"})
    if r.status_code == 501:
        pytest.skip("readout_validation unavailable in this interpreter")
    assert r.status_code == 200
    body = r.json()
    assert body["composite"] == _OBS_REF
    assert any(rr["name"] == "phantom-one" and rr["status"] == "not_in_structure"
               for rr in body["readouts"])


def test_linkage_index_200(client):
    """Empty workspace → always 200 with a typed (empty) payload."""
    r = client.get("/api/linkage-index")
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body and "edges" in body


def test_linkage_index_parity_with_server(tmp_path, monkeypatch):
    """FastAPI linkage route body == legacy server worker (source query)."""
    import yaml as _yaml
    import json as _json
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    inv = ws / "investigations" / "the-inv"
    inv.mkdir(parents=True)
    inv.joinpath("investigation.yaml").write_text(_yaml.safe_dump({
        "name": "the-inv", "studies": ["s1"],
        "acceptance_criteria": [{"study": "s1", "behavior": "b1"}],
    }))
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    sd.joinpath("study.yaml").write_text(_yaml.safe_dump({
        "name": "s1", "investigation": "the-inv", "cites": ["bib-X"],
        "tests": [{"name": "b1"}],
    }))
    legacy_bytes, legacy_status = srv.Handler._linkage_index_test(ws, source="bib-X")
    legacy_body = _json.loads(legacy_bytes)

    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    c = TestClient(app)
    r = c.get("/api/linkage-index", params={"source": "bib-X"})
    assert r.status_code == legacy_status == 200
    assert r.json() == legacy_body


def test_observables_routes_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/observables" in spec["paths"]
    assert "/api/study-observable-check" in spec["paths"]
    assert "/api/linkage-index" in spec["paths"]
    schemas = spec["components"]["schemas"]
    assert "ObservablesPayload" in schemas
    assert "StudyObservableCheck" in schemas
    assert "LinkageIndex" in schemas


# ---------------------------------------------------------------------------
# /api/composite-state (Batch 9)
# ---------------------------------------------------------------------------

import json as _json_cs


def _cs_ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    return ws


def _cs_client(ws):
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def _patch_cs_subprocess(monkeypatch, result):
    from vivarium_dashboard.lib import composite_state_views as _csv
    _csv.clear_cache()
    monkeypatch.setattr(_csv, "composite_state_via_subprocess", lambda ws, ref: result)


def test_composite_state_no_ref_400(client):
    r = client.get("/api/composite-state")
    assert r.status_code == 400
    assert r.json() == {"error": "ref required"}


def test_composite_state_unknown_ref_404(tmp_path, monkeypatch):
    ws = _cs_ws(tmp_path)
    _patch_cs_subprocess(monkeypatch, {"__not_registered__": True})
    r = _cs_client(ws).get("/api/composite-state", params={"ref": "nope.x"})
    assert r.status_code == 404
    body = r.json()
    assert body["unresolved"] is True
    assert body["ref"] == "nope.x"


def test_composite_state_spec_200(tmp_path, monkeypatch):
    ws = _cs_ws(tmp_path)
    (ws / "comp.yaml").write_text("a: 1\n", encoding="utf-8")
    _patch_cs_subprocess(monkeypatch, {"__not_registered__": True})
    r = _cs_client(ws).get("/api/composite-state", params={"ref": "comp.yaml"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "spec"
    assert body["state"] == {"a": 1}


def test_composite_state_path_form_200(tmp_path, monkeypatch):
    ws = _cs_ws(tmp_path)
    sd = ws / "reports" / "composite-state"
    sd.mkdir(parents=True)
    (sd / "myref.json").write_text(_json_cs.dumps({"state": {"x": 1}}), encoding="utf-8")
    _patch_cs_subprocess(monkeypatch, {"__not_registered__": True})
    # Path form with a trailing .json (the loom ?stateUrl= form).
    r = _cs_client(ws).get("/api/composite-state/myref.json")
    assert r.status_code == 200
    assert r.json()["kind"] == "spec"


def test_composite_state_static_fallback_200(tmp_path, monkeypatch):
    ws = _cs_ws(tmp_path)
    sd = ws / "reports" / "composite-state"
    sd.mkdir(parents=True)
    (sd / "gen.json").write_text(_json_cs.dumps({"state": {"y": 2}}), encoding="utf-8")
    _patch_cs_subprocess(monkeypatch, {"__build_error__": "boom"})
    r = _cs_client(ws).get("/api/composite-state", params={"ref": "gen"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "static-fallback"
    assert body["state"] == {"y": 2}


def test_composite_state_build_error_400(tmp_path, monkeypatch):
    ws = _cs_ws(tmp_path)
    _patch_cs_subprocess(monkeypatch, {"__build_error__": "boom"})
    r = _cs_client(ws).get("/api/composite-state", params={"ref": "gen"})
    assert r.status_code == 400
    assert r.json() == {"error": "generator build failed: boom"}


def test_composite_state_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert "/api/composite-state" in spec["paths"]
    assert "/api/composite-state/{ref}" in spec["paths"]
    assert "CompositeState" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# System & workspace routes — Batch 6
# ---------------------------------------------------------------------------

class TestFrameworkMetricsRoute:
    def test_empty_workspace_returns_200_and_typed_shape(self, client):
        r = client.get("/api/framework-metrics")
        assert r.status_code == 200
        body = r.json()
        assert "metrics" in body
        assert isinstance(body["metrics"], dict)
        assert body["n_investigations"] == 0
        assert body["n_studies"] == 0

    def test_framework_metrics_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "FrameworkMetrics" in components

    def test_delegates_to_lib_builder(self, client, monkeypatch, tmp_path):
        """Route body == lib builder output on the same workspace."""
        from vivarium_dashboard.lib.system_info import build_framework_metrics
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: tmp_path
        from fastapi.testclient import TestClient
        c = TestClient(app)
        r = c.get("/api/framework-metrics")
        assert r.status_code == 200
        assert r.json() == build_framework_metrics(tmp_path)


class TestGithubRepoRoute:
    def test_no_git_no_yaml_returns_null(self, client):
        r = client.get("/api/github-repo")
        assert r.status_code == 200
        assert r.json() == {"repo": None}

    def test_yaml_github_repo_returned(self, client, tmp_path):
        import yaml as _yaml
        (tmp_path / "workspace.yaml").write_text(_yaml.safe_dump({
            "dashboard": {"github_repo": "org/my-repo"},
        }))
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: tmp_path
        from fastapi.testclient import TestClient
        r = TestClient(app).get("/api/github-repo")
        assert r.status_code == 200
        assert r.json() == {"repo": "org/my-repo"}

    def test_github_repo_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "GithubRepo" in components


class TestUiConfigRoute:
    def test_defaults_on_empty_workspace(self, client):
        from vivarium_dashboard.lib.system_info import _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE
        r = client.get("/api/ui-config")
        assert r.status_code == 200
        body = r.json()
        assert body["composite_view"] == "bigraph-loom"
        assert body["ptools_server_url"] == ""
        assert body["ptools_omics_url_template"] == _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE

    def test_reads_workspace_yaml_ui_block(self, tmp_path):
        import yaml as _yaml
        (tmp_path / "workspace.yaml").write_text(_yaml.safe_dump({
            "ui": {
                "composite_view": "custom-view",
                "ptools_server_url": "http://ptools:1555",
            },
        }))
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: tmp_path
        from fastapi.testclient import TestClient
        body = TestClient(app).get("/api/ui-config").json()
        assert body["composite_view"] == "custom-view"
        assert body["ptools_server_url"] == "http://ptools:1555"

    def test_ui_config_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "UiConfig" in components

    def test_non_string_ui_field_degrades_to_raw_200(self, tmp_path):
        """A non-string ui.composite_view (off-spec) must NOT 500 — the route
        returns 200 with the raw builder dict (byte-identical to legacy)."""
        import yaml as _yaml
        (tmp_path / "workspace.yaml").write_text(_yaml.safe_dump({
            "ui": {"composite_view": 42},
        }))
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: tmp_path
        from fastapi.testclient import TestClient
        r = TestClient(app).get("/api/ui-config")
        assert r.status_code == 200
        # Raw value survives — UiConfig validation was bypassed via the fallback.
        assert r.json()["composite_view"] == 42


class TestWorkspaceHomeRoute:
    def test_empty_workspace_returns_200(self, client, tmp_path):
        r = client.get("/api/workspace")
        assert r.status_code == 200
        body = r.json()
        assert body["investigations"] == []
        assert body["imports"] == {}

    def test_returns_workspace_metadata(self, tmp_path):
        import yaml as _yaml
        (tmp_path / "workspace.yaml").write_text(_yaml.safe_dump({
            "name": "my-ws",
            "description": "Test workspace",
            "imports": {"pbg-core": "0.1.0"},
        }))
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: tmp_path
        from fastapi.testclient import TestClient
        body = TestClient(app).get("/api/workspace").json()
        assert body["name"] == "my-ws"
        assert body["description"] == "Test workspace"
        assert body["imports"] == {"pbg-core": "0.1.0"}

    def test_workspace_home_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "WorkspaceHome" in components


# ---------------------------------------------------------------------------
# Composite runs routes (Batch 10)
# ---------------------------------------------------------------------------

import json as _json_cr  # noqa: E402

from vivarium_dashboard.lib import composite_runs as _cr_lib  # noqa: E402


def _make_cr_workspace(tmp_path: Path, *, seed_run: bool = False,
                        run_id: str = "demo__1__aabbcc",
                        spec_id: str = "demo.spec",
                        status: str = "completed") -> Path:
    """Minimal workspace with optional composite-runs.db fixture."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".pbg").mkdir()
    if seed_run:
        db = ws / ".pbg" / "composite-runs.db"
        conn = _cr_lib.connect(db)
        _cr_lib.save_metadata(
            conn, spec_id=spec_id, run_id=run_id, params={}, label="lab",
            started_at=1_000_000.0, n_steps=5,
        )
        _cr_lib.complete_metadata(conn, run_id=run_id, n_steps=5, status=status)
        conn.close()
    return ws


def _cr_client(tmp_path: Path, *, seed_run: bool = False, **kw) -> "tuple":
    from fastapi.testclient import TestClient
    ws = _make_cr_workspace(tmp_path, seed_run=seed_run, **kw)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app), ws


class TestCompositeRunsRoute:
    def test_missing_spec_id_returns_400(self, tmp_path):
        c, _ = _cr_client(tmp_path)
        r = c.get("/api/composite-runs")
        assert r.status_code == 400
        body = r.json()
        assert body["runs"] == []
        assert "missing spec_id" in body["error"]

    def test_no_db_returns_200_empty(self, tmp_path):
        c, _ = _cr_client(tmp_path)
        r = c.get("/api/composite-runs?spec_id=demo.spec")
        assert r.status_code == 200
        assert r.json() == {"runs": []}

    def test_seeded_run_returns_list(self, tmp_path):
        c, _ = _cr_client(tmp_path, seed_run=True, spec_id="demo.spec",
                           run_id="r1")
        r = c.get("/api/composite-runs?spec_id=demo.spec")
        assert r.status_code == 200
        body = r.json()
        assert len(body["runs"]) == 1
        assert body["runs"][0]["run_id"] == "r1"

    def test_composite_runs_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "CompositeRunsList" in components


class TestCompositeRunRoute:
    def test_no_db_returns_404(self, tmp_path):
        c, _ = _cr_client(tmp_path)
        r = c.get("/api/composite-run/r1")
        assert r.status_code == 404
        assert r.json() == {"error": "no run database"}

    def test_no_history_returns_404(self, tmp_path):
        # DB exists but no history rows → "run not found"
        c, ws = _cr_client(tmp_path, seed_run=True, run_id="r1")
        r = c.get("/api/composite-run/r1")
        assert r.status_code == 404
        assert r.json() == {"error": "run not found"}

    def test_seeded_trajectory_returns_200(self, tmp_path):
        import sqlite3 as _sq
        c, ws = _cr_client(tmp_path, seed_run=True, run_id="r1")
        # Seed a history row manually
        db = ws / ".pbg" / "composite-runs.db"
        raw = _sq.connect(str(db))
        raw.execute(
            "CREATE TABLE IF NOT EXISTS history "
            "(simulation_id TEXT, step INTEGER, global_time REAL, "
            "state TEXT, PRIMARY KEY (simulation_id, step))"
        )
        raw.execute(
            "INSERT INTO history VALUES (?, ?, ?, ?)",
            ("r1", 0, 0.0, _json_cr.dumps({"v": 99})),
        )
        raw.commit()
        raw.close()
        r = c.get("/api/composite-run/r1")
        assert r.status_code == 200
        body = r.json()
        assert body["run_id"] == "r1"
        assert body["trajectory"][0]["state"] == {"v": 99}

    def test_composite_run_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "CompositeRunTrajectory" in components


class TestCompositeRunStateRoute:
    def test_no_db_returns_404(self, tmp_path):
        c, _ = _cr_client(tmp_path)
        r = c.get("/api/composite-run/r1/state")
        assert r.status_code == 404
        assert r.json() == {"error": "no run database"}

    def test_bad_step_returns_400(self, tmp_path):
        c, _ = _cr_client(tmp_path, seed_run=True, run_id="r1")
        r = c.get("/api/composite-run/r1/state?step=abc")
        assert r.status_code == 400
        assert r.json() == {"error": "step must be int"}

    def test_step_not_found_returns_404(self, tmp_path):
        c, _ = _cr_client(tmp_path, seed_run=True, run_id="r1")
        r = c.get("/api/composite-run/r1/state?step=99")
        assert r.status_code == 404
        assert r.json() == {"error": "state not found for run+step"}

    def test_seeded_step_returns_200(self, tmp_path):
        import sqlite3 as _sq
        c, ws = _cr_client(tmp_path, seed_run=True, run_id="r1")
        db = ws / ".pbg" / "composite-runs.db"
        raw = _sq.connect(str(db))
        raw.execute(
            "CREATE TABLE IF NOT EXISTS history "
            "(simulation_id TEXT, step INTEGER, global_time REAL, "
            "state TEXT, PRIMARY KEY (simulation_id, step))"
        )
        raw.execute(
            "INSERT INTO history VALUES (?, ?, ?, ?)",
            ("r1", 2, 2.0, _json_cr.dumps({"z": 3.14})),
        )
        raw.commit()
        raw.close()
        r = c.get("/api/composite-run/r1/state?step=2")
        assert r.status_code == 200
        body = r.json()
        assert body["run_id"] == "r1"
        assert body["step"] == 2
        assert body["state"] == {"z": 3.14}

    def test_composite_run_state_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "CompositeRunState" in components


class TestCompositeRunStatusRoute:
    def test_no_db_returns_404(self, tmp_path):
        c, _ = _cr_client(tmp_path)
        r = c.get("/api/composite-run/r1/status")
        assert r.status_code == 404
        assert r.json() == {"error": "no run database"}

    def test_unknown_run_returns_404(self, tmp_path):
        c, _ = _cr_client(tmp_path, seed_run=True, run_id="r1")
        r = c.get("/api/composite-run/no-such-run/status")
        assert r.status_code == 404
        assert r.json() == {"error": "run not found"}

    def test_completed_run_returns_200(self, tmp_path):
        c, _ = _cr_client(tmp_path, seed_run=True, run_id="r1",
                           status="completed")
        r = c.get("/api/composite-run/r1/status")
        assert r.status_code == 200
        body = r.json()
        assert body["run_id"] == "r1"
        assert body["status"] == "completed"
        assert body["n_steps"] == 5

    def test_completed_run_with_viz_html(self, tmp_path):
        c, ws = _cr_client(tmp_path, seed_run=True, run_id="r1",
                            status="completed")
        viz_dir = ws / ".pbg" / "runs" / "r1"
        viz_dir.mkdir(parents=True)
        (viz_dir / "viz.json").write_text(
            _json_cr.dumps({"plot": "xy"}), encoding="utf-8"
        )
        r = c.get("/api/composite-run/r1/status")
        assert r.status_code == 200
        assert r.json()["viz_html"] == {"plot": "xy"}

    def test_composite_run_status_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "CompositeRunStatus" in components

# Batch 11: /api/study-bigraph-paths
# ---------------------------------------------------------------------------

class TestStudyBigraphPaths:
    def test_missing_study_param_returns_400(self, client):
        r = client.get("/api/study-bigraph-paths")
        assert r.status_code == 400
        assert r.json() == {"error": "study slug required (?study=<slug>)"}
        assert "detail" not in r.json()

    def test_no_study_yaml_returns_404(self, client):
        r = client.get("/api/study-bigraph-paths?study=no-such-study")
        assert r.status_code == 404
        body = r.json()
        assert "error" in body
        assert "detail" not in body

    def test_happy_path_returns_200(self, client, monkeypatch):
        import vivarium_dashboard.api.app as _app
        monkeypatch.setattr(
            _app._study_viz, "build_study_bigraph_paths",
            lambda ws, slug, **kw: (
                {"composite": "c", "source_file": "f", "max_depth": 8,
                 "node_count": 2, "nodes": [{"path": "a"}, {"path": "b"}]},
                200,
            ),
        )
        r = client.get("/api/study-bigraph-paths?study=my-study")
        assert r.status_code == 200
        body = r.json()
        assert body["node_count"] == 2
        assert len(body["nodes"]) == 2

    def test_max_depth_parsed_defensively(self, client, monkeypatch):
        """Non-numeric max_depth falls back to 8 (mirrors legacy int(..., default 8))."""
        import vivarium_dashboard.api.app as _app
        captured: dict = {}

        def _cap(ws, slug, baseline_name="", max_depth=8):
            captured["max_depth"] = max_depth
            return {"error": "x"}, 400

        monkeypatch.setattr(_app._study_viz, "build_study_bigraph_paths", _cap)
        client.get("/api/study-bigraph-paths?study=s&max_depth=not_a_number")
        assert captured["max_depth"] == 8

    def test_study_bigraph_paths_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        assert "/api/study-bigraph-paths" in spec["paths"]
        assert "StudyBigraphPaths" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# Batch 11: /api/visualization-status
# ---------------------------------------------------------------------------

class TestVisualizationStatusRoute:
    def test_missing_name_returns_400(self, client):
        r = client.get("/api/visualization-status")
        assert r.status_code == 400
        assert r.json() == {"error": "missing name"}
        assert "detail" not in r.json()

    def test_name_not_in_workspace_returns_200_missing(self, client, tmp_path, monkeypatch):
        import vivarium_dashboard.api.app as _app
        monkeypatch.setattr(
            _app._study_viz, "build_visualization_status",
            lambda ws, name: ({"status": "missing", "name": name}, 200),
        )
        r = client.get("/api/visualization-status?name=nonexistent")
        assert r.status_code == 200
        assert r.json()["status"] == "missing"
        assert r.json()["name"] == "nonexistent"

    def test_happy_path_described(self, client, tmp_path, monkeypatch):
        import vivarium_dashboard.api.app as _app
        monkeypatch.setattr(
            _app._study_viz, "build_visualization_status",
            lambda ws, name: ({
                "status": "described",
                "name": name,
                "has_request": False,
                "has_response": False,
                "has_staged": False,
                "has_committed": False,
            }, 200),
        )
        r = client.get("/api/visualization-status?name=my_viz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "described"
        assert body["has_committed"] is False

    def test_visualization_status_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        assert "/api/visualization-status" in spec["paths"]
        assert "VisualizationStatus" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# Batch 11: /api/visualization-instances
# ---------------------------------------------------------------------------

class TestVisualizationInstancesRoute:
    def test_empty_workspace_returns_empty_instances(self, client):
        r = client.get("/api/visualization-instances")
        assert r.status_code == 200
        assert r.json() == {"instances": []}

    def test_happy_path(self, client, monkeypatch):
        import vivarium_dashboard.api.app as _app
        monkeypatch.setattr(
            _app._study_viz, "build_visualization_instances",
            lambda ws: {"instances": [
                {"name": "my_viz", "class": "TimeSeriesPlot",
                 "address": "local:TimeSeriesPlot", "config": {}, "description": ""}
            ]},
        )
        r = client.get("/api/visualization-instances")
        assert r.status_code == 200
        body = r.json()
        assert len(body["instances"]) == 1
        assert body["instances"][0]["class"] == "TimeSeriesPlot"

    def test_visualization_instances_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        assert "/api/visualization-instances" in spec["paths"]
        assert "VisualizationInstances" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# Batch 11: /api/ptools-launch/{study}
# ---------------------------------------------------------------------------

class TestPtoolsLaunchRoute:
    def test_invalid_slug_returns_400(self, client):
        r = client.get("/api/ptools-launch/INVALID-SLUG")
        assert r.status_code == 400
        assert r.json() == {"error": "invalid study name"}
        assert "detail" not in r.json()

    def test_no_ptools_config_returns_400(self, client, monkeypatch):
        import vivarium_dashboard.api.app as _app
        monkeypatch.setattr(
            _app._study_viz, "build_ptools_launch",
            lambda ws, study, **kw: ({"error": "ptools_server_url not configured"}, 400),
        )
        r = client.get("/api/ptools-launch/my-study")
        assert r.status_code == 400
        assert r.json() == {"error": "ptools_server_url not configured"}
        assert "detail" not in r.json()

    def test_study_not_found_returns_404(self, client, monkeypatch):
        import vivarium_dashboard.api.app as _app
        monkeypatch.setattr(
            _app._study_viz, "build_ptools_launch",
            lambda ws, study, **kw: ({"error": f"study not found: {study}"}, 404),
        )
        r = client.get("/api/ptools-launch/my-study")
        assert r.status_code == 404
        assert "study not found" in r.json()["error"]
        assert "detail" not in r.json()

    def test_happy_path_returns_200(self, client, monkeypatch):
        import vivarium_dashboard.api.app as _app
        monkeypatch.setattr(
            _app._study_viz, "build_ptools_launch",
            lambda ws, study, **kw: ({
                "url": "http://ptools.example.com/omics?omics=t",
                "tsv_url": "http://dash.example.com/studies/s/ptools/f.tsv",
                "available": ["studies/s/ptools/f.tsv"],
            }, 200),
        )
        r = client.get("/api/ptools-launch/my-study")
        assert r.status_code == 200
        body = r.json()
        assert "url" in body
        assert "tsv_url" in body
        assert "available" in body

    def test_ptools_launch_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        assert "/api/ptools-launch/{study}" in spec["paths"]
        assert "PtoolsLaunch" in spec["components"]["schemas"]

# /api/pending
# ---------------------------------------------------------------------------

class TestPendingRoute:
    def test_pending_empty_workspace_returns_200(self, client):
        """A non-git tmp workspace → 200 with EXACTLY {} (byte-identical to legacy).

        PendingEntries is a pure pass-through (no declared fields), so the
        builder's ``{}`` is NOT inflated into the 7-empty-panel structure.
        """
        r = client.get("/api/pending")
        assert r.status_code == 200
        assert r.json() == {}

    def test_pending_typed_passthrough(self, client, monkeypatch):
        """A full pending payload validates through PendingEntries (extra='allow')."""
        import vivarium_dashboard.api.app as _app

        payload = {
            "observables": [{"entry": {"name": "obs-b"}, "branch": "stage/obs-b"}],
            "visualizations": [],
            "phases": [],
            "datasets": [],
            "references_pdfs": [],
            "expert_docs": [],
            "imports": [],
        }
        monkeypatch.setattr(_app._work_views, "build_pending",
                            lambda ws: (payload, 200))
        r = client.get("/api/pending")
        assert r.status_code == 200
        body = r.json()
        assert body["observables"][0]["entry"]["name"] == "obs-b"
        assert body["observables"][0]["branch"] == "stage/obs-b"

    def test_pending_500_on_exception(self, client, monkeypatch):
        """An unexpected exception in build_pending → HTTP 500 with {error}."""
        import vivarium_dashboard.api.app as _app

        monkeypatch.setattr(_app._work_views, "build_pending",
                            lambda ws: ({"error": "boom"}, 500))
        r = client.get("/api/pending")
        assert r.status_code == 500
        assert r.json()["error"] == "boom"

    def test_pending_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        assert "/api/pending" in spec["paths"]
        for name in ("PendingEntries",):
            assert name in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/generation
# ---------------------------------------------------------------------------

class TestGenerationRoute:
    def test_generation_null_when_empty(self, client, monkeypatch):
        """An empty workspace (no pbg_superpowers generation) → {generation: null}."""
        import vivarium_dashboard.api.app as _app

        monkeypatch.setattr(_app._work_views, "build_generation",
                            lambda ws: {"generation": None})
        r = client.get("/api/generation")
        assert r.status_code == 200
        assert r.json() == {"generation": None}

    def test_generation_typed_passthrough(self, client, monkeypatch):
        """A full generation summary validates through Generation."""
        import vivarium_dashboard.api.app as _app

        gen_data = {
            "generation": {
                "generation_id": "gen-001",
                "git_sha": "abc123",
                "param_set_hash": "hashXYZ",
                "created_at": "2026-06-25T00:00:00",
                "label": "round-1",
                "n_runs": 5,
            }
        }
        monkeypatch.setattr(_app._work_views, "build_generation",
                            lambda ws: gen_data)
        r = client.get("/api/generation")
        assert r.status_code == 200
        body = r.json()
        assert body["generation"]["generation_id"] == "gen-001"
        assert body["generation"]["n_runs"] == 5

    def test_generation_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        assert "/api/generation" in spec["paths"]
        for name in ("Generation", "GenerationSummary"):
            assert name in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# /api/work-composite-diff
# ---------------------------------------------------------------------------

class TestWorkCompositeDiffRoute:
    def test_work_composite_diff_empty_workspace_returns_200(self, client):
        """An empty tmp workspace → 200 with error in body (non-git dir)."""
        r = client.get("/api/work-composite-diff")
        assert r.status_code == 200
        body = r.json()
        assert "changes" in body
        assert "base" in body

    def test_work_composite_diff_typed_passthrough(self, client, monkeypatch):
        """A full diff payload validates through WorkCompositeDiff."""
        import vivarium_dashboard.api.app as _app

        payload = {
            "base": "main",
            "branch": "feat/my-feature",
            "changes": [
                {
                    "path": "composites/my_composite.py",
                    "lines_added": 42,
                    "lines_removed": 3,
                    "category": "composite",
                }
            ],
        }
        monkeypatch.setattr(_app._work_views, "build_work_composite_diff",
                            lambda ws: payload)
        r = client.get("/api/work-composite-diff")
        assert r.status_code == 200
        body = r.json()
        assert body["branch"] == "feat/my-feature"
        assert len(body["changes"]) == 1
        assert body["changes"][0]["category"] == "composite"
        assert body["changes"][0]["lines_added"] == 42

    def test_work_composite_diff_error_in_body(self, client, monkeypatch):
        """On merge-base failure the response is still 200 with error in body."""
        import vivarium_dashboard.api.app as _app

        monkeypatch.setattr(_app._work_views, "build_work_composite_diff",
                            lambda ws: {
                                "base": "main", "branch": "",
                                "changes": [],
                                "error": "merge-base failed: not a git repo",
                            })
        r = client.get("/api/work-composite-diff")
        assert r.status_code == 200
        body = r.json()
        assert body["error"] == "merge-base failed: not a git repo"
        assert body["changes"] == []

    def test_work_composite_diff_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        assert "/api/work-composite-diff" in spec["paths"]
        for name in ("WorkCompositeDiff", "WorkCompositeDiffEntry"):
            assert name in spec["components"]["schemas"]

# Workspace & source routes — Batch 13
# ---------------------------------------------------------------------------

class TestSourceBuildsRoute:
    def test_always_200(self, client, monkeypatch):
        """GET /api/source/builds always returns HTTP 200."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        monkeypatch.setattr(wdv, "build_source_builds", lambda: {"builds": [], "error": None})
        r = client.get("/api/source/builds")
        assert r.status_code == 200

    def test_returns_builds_list_and_error(self, client, monkeypatch):
        """Body carries builds[] and error (null or string)."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        monkeypatch.setattr(wdv, "build_source_builds", lambda: {"builds": [], "error": None})
        body = client.get("/api/source/builds").json()
        assert "builds" in body
        assert isinstance(body["builds"], list)

    def test_degraded_when_sms_api_down(self, client, monkeypatch):
        """When sms-api is down, builds=[] and error carries a reason (still 200)."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        monkeypatch.setattr(
            wdv, "build_source_builds",
            lambda: {"builds": [], "error": "connection refused"},
        )
        r = client.get("/api/source/builds")
        assert r.status_code == 200
        body = r.json()
        assert body["builds"] == []
        assert body["error"] == "connection refused"

    def test_source_builds_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "SourceBuilds" in components


class TestWorkspacesRoute:
    def test_always_200(self, client, tmp_path, monkeypatch):
        """GET /api/workspaces always returns HTTP 200."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        monkeypatch.setattr(
            wdv, "build_workspaces",
            lambda root: {"current": {"name": "ws", "path": str(root)}, "workspaces": []},
        )
        r = client.get("/api/workspaces")
        assert r.status_code == 200

    def test_body_shape(self, client, monkeypatch):
        """Body has current{name,path} and workspaces[]."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        monkeypatch.setattr(
            wdv, "build_workspaces",
            lambda root: {
                "current": {"name": "my-ws", "path": "/some/path"},
                "workspaces": [
                    {"name": "my-ws", "path": "/some/path", "repo": "my-ws",
                     "branch": "main", "commit": "abc123", "label": "my-ws",
                     "status": "current"},
                ],
            },
        )
        body = client.get("/api/workspaces").json()
        assert "current" in body
        assert "workspaces" in body
        assert body["current"]["name"] == "my-ws"
        assert isinstance(body["workspaces"], list)

    def test_workspaces_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "WorkspacesList" in components


class TestSystemDepsCheckRoute:
    def test_400_missing_name(self, client, monkeypatch):
        """GET /api/system-deps-check without ?name= → 400."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        monkeypatch.setattr(wdv, "module_registry", lambda root: [])
        r = client.get("/api/system-deps-check")
        assert r.status_code == 400
        assert r.json() == {"error": "name required"}

    def test_404_unknown_module(self, client, monkeypatch):
        """GET /api/system-deps-check?name=ghost → 404."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        monkeypatch.setattr(wdv, "module_registry", lambda root: [])
        r = client.get("/api/system-deps-check?name=ghost")
        assert r.status_code == 404
        assert "unknown module" in r.json()["error"]
        assert "ghost" in r.json()["error"]

    def test_200_ok_trivial(self, client, monkeypatch):
        """GET /api/system-deps-check?name=pbg-trivial → 200 with ok=True."""
        from vivarium_dashboard.lib import workspace_deps_views as wdv
        catalog = [{"name": "pbg-trivial", "system_dependencies": {"checks": []}}]
        monkeypatch.setattr(wdv, "module_registry", lambda root: catalog)
        r = client.get("/api/system-deps-check?name=pbg-trivial")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "pbg-trivial"
        assert body["ok"] is True
        assert body["checks"] == []
        assert "platform" in body

    def test_system_deps_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "SystemDepsCheck" in components


# ===========================================================================
# Batch 14: investigation-state-tree (typed JSON) + the download routes
# ===========================================================================

import yaml as _yaml14


class TestInvestigationStateTreeRoute:
    def _write_composite(self, ws, inv="my-inv", comp="c", state=None):
        d = ws / "studies" / inv / "composites"
        d.mkdir(parents=True, exist_ok=True)
        body = {"state": state} if state is not None else {"process": "P"}
        (d / f"{comp}.yaml").write_text(_yaml14.dump(body), encoding="utf-8")

    def test_200_nodes(self, client, tmp_path):
        self._write_composite(
            tmp_path, state={"s": {"_type": "float", "_default": 0.0}}
        )
        r = client.get("/api/investigation-state-tree?investigation=my-inv&composite=c")
        assert r.status_code == 200
        body = r.json()
        assert "nodes" in body
        assert any(n["path"] == ["s"] for n in body["nodes"])

    def test_400_missing_args(self, client):
        r = client.get("/api/investigation-state-tree")
        assert r.status_code == 400
        assert r.json() == {"error": "investigation + composite required"}

    def test_404_not_found(self, client):
        r = client.get("/api/investigation-state-tree?investigation=x&composite=ghost")
        assert r.status_code == 404
        assert "not found" in r.json()["error"]

    def test_in_openapi(self, client):
        components = client.get("/openapi.json").json()["components"]["schemas"]
        assert "InvestigationStateTree" in components
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/investigation-state-tree" in paths


class TestStudyExportRoute:
    def _make_study(self, ws, name="s1"):
        d = ws / "studies" / name
        d.mkdir(parents=True)
        (d / "study.yaml").write_text("name: s1\n", encoding="utf-8")
        (d / "data.txt").write_text("hello", encoding="utf-8")

    def test_200_zip(self, client, tmp_path):
        self._make_study(tmp_path)
        r = client.get("/api/study-export?study=s1")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert r.headers["content-disposition"] == 'attachment; filename="s1.zip"'
        import io, zipfile
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert any(n.endswith("data.txt") for n in zf.namelist())

    def test_400_missing(self, client):
        r = client.get("/api/study-export")
        assert r.status_code == 400
        assert r.json() == {"error": "missing study"}

    def test_404_not_found(self, client):
        r = client.get("/api/study-export?study=ghost")
        assert r.status_code == 404
        assert r.json() == {"error": "study not found"}

    def test_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/study-export" in paths


class TestDataSourceFileRoute:
    def test_200_inline_text(self, client, monkeypatch):
        from vivarium_dashboard.lib import download_views as dv
        monkeypatch.setattr(
            dv, "resolve_data_source_file",
            lambda ws, key: (b"a\tb\n", "text/tab-separated-values; charset=utf-8", True, "t.tsv"),
        )
        r = client.get("/api/data-source-file?key=k1")
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/tab-separated-values; charset=utf-8"
        assert r.headers["cache-control"] == "no-store"
        assert "content-disposition" not in r.headers   # inline → no attachment
        assert r.content == b"a\tb\n"

    def test_200_binary_attachment(self, client, monkeypatch):
        from vivarium_dashboard.lib import download_views as dv
        monkeypatch.setattr(
            dv, "resolve_data_source_file",
            lambda ws, key: (b"\x00\x01", "application/octet-stream", False, "blob.bin"),
        )
        r = client.get("/api/data-source-file?key=k1")
        assert r.status_code == 200
        assert r.headers["content-disposition"] == 'attachment; filename="blob.bin"'
        assert r.headers["cache-control"] == "no-store"

    def test_400_missing_key(self, client):
        r = client.get("/api/data-source-file")
        assert r.status_code == 400
        assert r.json() == {"error": "missing ?key="}

    def test_404_unknown_key(self, client, monkeypatch):
        from vivarium_dashboard.lib import download_views as dv
        def _raise(ws, key):
            raise dv.DownloadError({"error": f"key not in data-source bundle: {key!r}"}, 404)
        monkeypatch.setattr(dv, "resolve_data_source_file", _raise)
        r = client.get("/api/data-source-file?key=ghost")
        assert r.status_code == 404
        assert "ghost" in r.json()["error"]

    def test_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/data-source-file" in paths


class TestIsetReportRoute:
    def test_200_html(self, client, tmp_path):
        rep = tmp_path / "investigations" / "inv-a" / "reports"
        rep.mkdir(parents=True)
        (rep / "index.html").write_text("<html>r</html>", encoding="utf-8")
        r = client.get("/api/iset/inv-a/report")
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/html"
        assert r.headers["cache-control"] == "no-store"  # mirrors stdlib _serve_file
        assert r.text == "<html>r</html>"

    def test_404_no_report(self, client):
        r = client.get("/api/iset/ghost/report")
        assert r.status_code == 404
        assert "ghost" in r.json()["error"]

    def test_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/iset/{slug}/report" in paths


class TestGuidanceRoute:
    def test_200_latest_html(self, client, tmp_path):
        content = tmp_path / ".pbg" / "server" / "content"
        content.mkdir(parents=True)
        (content / "g.html").write_text("<html>guide</html>", encoding="utf-8")
        r = client.get("/api/guidance")
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/html"
        assert r.headers["cache-control"] == "no-store"  # mirrors stdlib _serve_file
        assert r.text == "<html>guide</html>"

    def test_204_when_absent(self, client):
        r = client.get("/api/guidance")
        assert r.status_code == 204
        assert r.content == b""

    def test_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/guidance" in paths


class TestInvestigationNotebookRoute:
    def test_200_download(self, client, monkeypatch):
        from vivarium_dashboard.lib import download_views as dv
        monkeypatch.setattr(
            dv, "build_investigation_notebook",
            lambda ws, slug, fmt: (b"print(1)\n", "text/x-python", "inv.py"),
        )
        r = client.get("/api/investigation-notebook/inv?format=py")
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/x-python"
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["content-disposition"] == 'attachment; filename="inv.py"'
        assert r.content == b"print(1)\n"

    def test_404_unknown(self, client, monkeypatch):
        from vivarium_dashboard.lib import download_views as dv
        def _raise(ws, slug, fmt):
            raise dv.DownloadError({"error": f"no investigation {slug!r}"}, 404)
        monkeypatch.setattr(dv, "build_investigation_notebook", _raise)
        r = client.get("/api/investigation-notebook/ghost")
        assert r.status_code == 404
        assert "ghost" in r.json()["error"]

    def test_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/investigation-notebook/{slug}" in paths


# ---------------------------------------------------------------------------
# /api/events — SSE workspace-state stream (Phase C, Batch 15)
# ---------------------------------------------------------------------------

class TestEventsRoute:
    def test_events_headers_and_first_chunk(self, client, tmp_path, monkeypatch):
        """SSE response has correct headers and first event has correct SSE framing.

        The TestClient blocks until the ASGI generator finishes, so we
        monkeypatch ``workspace_state_stream`` to a finite one-shot generator.
        The real infinite loop is covered by ``tests/test_events_lib.py``.
        """
        import yaml as _yaml
        import vivarium_dashboard.lib.events as _ev

        (tmp_path / "workspace.yaml").write_text(
            _yaml.safe_dump({"name": "test-ws"}), encoding="utf-8"
        )

        # One-shot: yield the first event and stop so the TestClient completes.
        async def _one_shot(ws_root, *, poll_interval: float = 1.0):
            yield (
                b"event: state\ndata: "
                + _ev.workspace_state_payload(ws_root).encode()
                + b"\n\n"
            )

        monkeypatch.setattr(_ev, "workspace_state_stream", _one_shot)

        r = client.get("/api/events")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-store"
        lines = r.text.split("\n")
        assert lines[0].startswith("event: state")

    def test_events_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/events" in paths


# ---------------------------------------------------------------------------
# Static + SPA-shell serving (Phase C, Batch 16)
# ---------------------------------------------------------------------------

class TestStaticRoutes:
    def test_index_shell_renders_then_serves(self, client, tmp_path, monkeypatch):
        """GET / re-renders (best-effort) then serves reports/index.html as
        text/html + Cache-Control: no-store."""
        import vivarium_dashboard.lib.report as _report
        calls = []
        monkeypatch.setattr(_report, "render_workspace_report", lambda ws: calls.append(ws))
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "index.html").write_text("<html>shell</html>")
        r = client.get("/")
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/html"
        assert r.headers["cache-control"] == "no-store"
        assert r.text == "<html>shell</html>"
        assert calls, "render_workspace_report should have been called"

    def test_index_shell_render_failure_is_nonblocking(self, client, tmp_path, monkeypatch):
        """A render exception never blocks the load — the on-disk file still serves."""
        import vivarium_dashboard.lib.report as _report

        def _boom(ws):
            raise RuntimeError("render kaboom")

        monkeypatch.setattr(_report, "render_workspace_report", _boom)
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "index.html").write_text("ondisk")
        r = client.get("/")
        assert r.status_code == 200
        assert r.text == "ondisk"

    def test_index_shell_404_when_absent(self, client, tmp_path, monkeypatch):
        import vivarium_dashboard.lib.report as _report
        monkeypatch.setattr(_report, "render_workspace_report", lambda ws: None)
        r = client.get("/")
        assert r.status_code == 404

    def test_catch_all_serves_reports_file(self, client, tmp_path):
        """A reports/ file resolves via the catch-all with the guessed mime."""
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "foo.txt").write_text("hello")
        r = client.get("/foo.txt")
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/plain"
        assert r.headers["cache-control"] == "no-store"
        assert r.text == "hello"
        # Byte-identical header set to the stdlib _serve_file: a plain
        # Response(read_bytes), NOT FileResponse — so none of FileResponse's
        # ETag / Last-Modified / Accept-Ranges (which would enable conditional
        # 304s / Range 206s the legacy handler never did).
        assert "etag" not in r.headers
        assert "last-modified" not in r.headers
        assert "accept-ranges" not in r.headers

    def test_catch_all_serves_bundled_first(self, client, tmp_path, monkeypatch):
        """A bundled STATIC_DIR file wins (step 1) and serves as application/javascript."""
        static_dir = tmp_path / "bundled"
        static_dir.mkdir()
        (static_dir / "client.js").write_text("// bundled")
        monkeypatch.setattr(api_app._static_serving, "STATIC_DIR", static_dir)
        r = client.get("/client.js")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/javascript"
        assert r.headers["cache-control"] == "no-store"
        assert r.text == "// bundled"

    def test_catch_all_missing_asset_404(self, client):
        r = client.get("/definitely-not-here.txt")
        assert r.status_code == 404

    def test_catch_all_traversal_403(self, client):
        # Percent-encode the dots so httpx doesn't normalize the dot-segments
        # away before they reach the route; Starlette decodes them into a literal
        # ".." segment in `rel`, which the route's guard rejects with 403.
        r = client.get("/%2e%2e/etc/passwd")
        assert r.status_code == 403

    def test_bigraph_loom_traversal_403(self, client):
        r = client.get("/bigraph-loom/%2e%2e/secret")
        assert r.status_code == 403

    def test_bigraph_loom_serves_asset(self, client, tmp_path, monkeypatch):
        loom_dir = tmp_path / "loom"
        loom_dir.mkdir()
        (loom_dir / "index.html").write_text("<loom/>")
        monkeypatch.setattr("bigraph_loom.asset_dir", lambda: loom_dir, raising=False)
        r = client.get("/bigraph-loom/")
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/html"
        assert r.headers["cache-control"] == "no-store"
        assert r.text == "<loom/>"

    def test_parsimony_404_when_no_dir(self, client, monkeypatch):
        monkeypatch.setattr(api_app._static_serving, "parsimony_viewer_dir", lambda: None)
        r = client.get("/parsimony-viewer/")
        assert r.status_code == 404

    def test_parsimony_serves_when_present(self, client, tmp_path, monkeypatch):
        pv = tmp_path / "pv"
        pv.mkdir()
        (pv / "index.html").write_text("<pv/>")
        monkeypatch.setattr(api_app._static_serving, "parsimony_viewer_dir", lambda: pv)
        r = client.get("/parsimony-viewer/")
        assert r.status_code == 200
        assert r.text == "<pv/>"
        assert r.headers["cache-control"] == "no-store"

    def test_catch_all_does_not_shadow_api_routes(self, client):
        """CRITICAL: the catch-all (registered LAST) must not shadow /api/*.

        GET /api/config still returns the typed config JSON (the specific route
        wins), and an unknown /api/nope is a 404 (not a 200 catch-all body)."""
        r = client.get("/api/config")
        assert r.status_code == 200
        assert r.json() == {"mode": "local-server", "basePath": None}
        r2 = client.get("/api/nope")
        assert r2.status_code == 404

    def test_catch_all_registered_last(self):
        """The catch-all '/{rel:path}' is the final registered GET route."""
        app = create_app()
        # Collect routes with a path; the catch-all must be the last GET route.
        get_routes = [
            r for r in app.router.routes
            if getattr(r, "methods", None) and "GET" in r.methods
        ]
        assert get_routes[-1].path == "/{rel:path}", (
            f"catch-all not last; last route is {get_routes[-1].path}"
        )


class TestStudyDetailPageRoute:
    """Tests for GET /studies/{slug} (Phase C, Batch 17)."""

    @pytest.fixture
    def ws_with_study(self, tmp_path):
        """Workspace with one study (v2 variants shape — passes spec validator)."""
        import yaml
        slug = "dnaa-01-binding"
        inv = tmp_path / "investigations" / slug
        inv.mkdir(parents=True)
        (inv / "spec.yaml").write_text(yaml.safe_dump({
            "name": slug,
            "title": "DnaA Binding Study",
            "baseline": "dnaa-baseline",
            "status": "draft",
            "objective": "Test DnaA binding kinetics.",
            "question": "", "hypothesis": "",
            "comparisons": [], "conclusions": "",
            "variants": [
                {
                    "name": "dnaa-baseline",
                    "source": "pbg_basic_processes.composites.test.dummy",
                    "document": "./composites/dnaa-baseline.yaml",
                },
            ],
            "runs": [],
        }), encoding="utf-8")
        return tmp_path, slug

    @pytest.fixture
    def client_with_study(self, ws_with_study):
        """TestClient whose workspace has a real study."""
        ws, slug = ws_with_study
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        return TestClient(app), slug

    def test_valid_study_returns_200_html(self, client_with_study):
        client, slug = client_with_study
        r = client.get(f"/studies/{slug}")
        assert r.status_code == 200
        ct = r.headers["content-type"]
        assert "text/html" in ct
        assert "charset=utf-8" in ct

    def test_valid_study_body_contains_study_content(self, client_with_study):
        client, slug = client_with_study
        r = client.get(f"/studies/{slug}")
        assert r.status_code == 200
        # Template renders the slug and/or study title
        assert slug in r.text or "DnaA Binding Study" in r.text

    def test_invalid_slug_returns_404_not_found(self, client_with_study):
        """Invalid slug → 404 with exact legacy body (not a catch-all 404)."""
        client, _ = client_with_study
        r = client.get("/studies/../etc/passwd")
        # Starlette normalises .. out of the URL before it reaches the route;
        # the router may produce a 307 redirect or 404 — in both cases the slug
        # validation must ultimately prevent a 200 render.
        assert r.status_code in (404, 307, 400)

    def test_unknown_slug_returns_404_study_not_found(self, client_with_study):
        """Unknown (but valid) slug → 404 with 'Study not found' body."""
        client, _ = client_with_study
        r = client.get("/studies/does-not-exist")
        assert r.status_code == 404
        assert "Study not found" in r.text
        assert "does-not-exist" in r.text

    def test_invalid_slug_uppercase_returns_404(self, client_with_study):
        """An uppercase slug fails validation → 404 Not found."""
        client, _ = client_with_study
        r = client.get("/studies/BadSlug")
        assert r.status_code == 404
        assert "<h1>Not found</h1>" in r.text

    def test_no_cache_control_header(self, client_with_study):
        """_send_html does NOT set Cache-Control; the FastAPI route must not either."""
        client, slug = client_with_study
        r = client.get(f"/studies/{slug}")
        assert r.status_code == 200
        # cache-control must be absent (not 'no-store' like _serve_file)
        assert "cache-control" not in r.headers

    def test_study_route_before_catch_all(self):
        """/studies/{slug} must be registered BEFORE the catch-all /{rel:path}."""
        app = create_app()
        get_routes = [
            r for r in app.router.routes
            if getattr(r, "methods", None) and "GET" in r.methods
        ]
        paths = [r.path for r in get_routes]
        assert "/studies/{slug}" in paths, "/studies/{slug} not registered"
        study_idx = paths.index("/studies/{slug}")
        catch_all_idx = paths.index("/{rel:path}")
        assert study_idx < catch_all_idx, (
            f"/studies/{{slug}} (idx {study_idx}) must come before "
            f"catch-all (idx {catch_all_idx})"
        )

    def test_study_route_does_not_fall_through_to_catch_all(self, client_with_study):
        """A valid /studies/<slug> must hit the study route, not the catch-all.

        The catch-all would return 404 (file not found) with Cache-Control:
        no-store. A successful study page is a 200 HTML with the study name.
        An unknown slug produces the 'Study not found' HTML — NOT an asset 404.
        """
        client, _ = client_with_study
        r = client.get("/studies/some-unknown-but-valid-slug")
        assert r.status_code == 404
        # The study-page 404 has 'Study not found'; the catch-all 404 is empty.
        assert "Study not found" in r.text


# ---------------------------------------------------------------------------
# Batch 20: Study lifecycle POST routes
# ---------------------------------------------------------------------------


class TestBatch20LifecycleRoutes:
    """Route-level tests for Batch 20 study lifecycle POST endpoints."""

    @pytest.fixture
    def ws(self, tmp_path: Path) -> Path:
        w = tmp_path / "ws"
        w.mkdir()
        (w / "workspace.yaml").write_text(
            "schema_version: 2\nname: ws\ncreated: '2026-01-01'\nplugin_version: 0.6.1\npackage_path: pkg\n"
        )
        (w / "studies").mkdir()
        (w / "investigations").mkdir()
        return w

    @pytest.fixture
    def lc_client(self, ws: Path) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        return TestClient(app)

    def _ws(self, lc_client: TestClient) -> Path:
        return lc_client.app.dependency_overrides[get_workspace]()

    # -----------------------------------------------------------------------
    # /api/feedback-apply-action
    # -----------------------------------------------------------------------

    def test_feedback_apply_action_missing_item_id(self, lc_client: TestClient) -> None:
        r = lc_client.post("/api/feedback-apply-action", json={})
        assert r.status_code == 400
        assert "error" in r.json()

    def test_feedback_apply_action_unknown_item(self, lc_client: TestClient) -> None:
        r = lc_client.post("/api/feedback-apply-action", json={"item_id": "deadbeef"})
        assert r.status_code == 400
        assert "error" in r.json()

    def test_feedback_apply_action_in_openapi(self, lc_client: TestClient) -> None:
        schema = lc_client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/api/feedback-apply-action" in paths
        assert "post" in paths["/api/feedback-apply-action"]

    # -----------------------------------------------------------------------
    # /api/study-create-from-run
    # -----------------------------------------------------------------------

    def test_study_create_from_run_missing_fields(self, lc_client: TestClient) -> None:
        r = lc_client.post("/api/study-create-from-run", json={"name": "x"})
        assert r.status_code == 400

    def test_study_create_from_run_no_scratch_db(self, lc_client: TestClient) -> None:
        r = lc_client.post(
            "/api/study-create-from-run",
            json={"name": "test-study", "source_run_id": "rid1"},
        )
        assert r.status_code == 404
        assert "error" in r.json()

    def test_study_create_from_run_happy(self, lc_client: TestClient, ws: Path) -> None:
        import sqlite3

        pbg = ws / ".pbg"
        pbg.mkdir()
        db = pbg / "composite-runs.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE runs_meta (
                run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, label TEXT,
                params_json TEXT, started_at REAL NOT NULL, completed_at REAL,
                n_steps INTEGER, status TEXT NOT NULL
            );
            CREATE TABLE history (
                simulation_id TEXT NOT NULL, step INTEGER NOT NULL,
                global_time REAL, state TEXT NOT NULL,
                PRIMARY KEY (simulation_id, step)
            );
        """)
        conn.execute(
            "INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("rid1", "pkg.foo", "t", "{}", 1.0, 2.0, 5, "completed"),
        )
        conn.commit()
        conn.close()
        r = lc_client.post(
            "/api/study-create-from-run",
            json={"name": "created-study", "source_run_id": "rid1", "objective": "Q?"},
        )
        assert r.status_code == 200
        assert r.json()["study"] == "created-study"

    def test_study_create_from_run_in_openapi(self, lc_client: TestClient) -> None:
        schema = lc_client.get("/openapi.json").json()
        assert "/api/study-create-from-run" in schema["paths"]

    # -----------------------------------------------------------------------
    # /api/study-rename
    # -----------------------------------------------------------------------

    def test_study_rename_missing_fields(self, lc_client: TestClient) -> None:
        r = lc_client.post("/api/study-rename", json={"study": "x"})
        assert r.status_code == 400

    def test_study_rename_not_found(self, lc_client: TestClient) -> None:
        r = lc_client.post(
            "/api/study-rename", json={"study": "nope", "new_name": "something"}
        )
        assert r.status_code == 404

    def test_study_rename_happy(self, lc_client: TestClient, ws: Path) -> None:
        import yaml

        d = ws / "studies" / "old-study"
        d.mkdir()
        (d / "study.yaml").write_text(
            yaml.safe_dump({"name": "old-study", "status": "active"}, sort_keys=False)
        )
        r = lc_client.post(
            "/api/study-rename", json={"study": "old-study", "new_name": "new-study"}
        )
        assert r.status_code == 200
        assert r.json()["name"] == "new-study"

    # -----------------------------------------------------------------------
    # /api/study-sync-runs
    # -----------------------------------------------------------------------

    def test_study_sync_runs_missing_slug(self, lc_client: TestClient) -> None:
        r = lc_client.post("/api/study-sync-runs", json={})
        assert r.status_code == 400

    def test_study_sync_runs_unknown_study(self, lc_client: TestClient) -> None:
        r = lc_client.post("/api/study-sync-runs", json={"study": "nope"})
        assert r.status_code == 404

    def test_study_sync_runs_happy(self, lc_client: TestClient, ws: Path) -> None:
        from pbg_superpowers import run_registry, study_io

        d = ws / "studies" / "sync-s"
        d.mkdir()
        study_io.save_yaml_atomic(d / "study.yaml", {"name": "sync-s", "runs": []})
        run_registry.register_run(
            d / "runs.db", "r1", spec_id="s1", status="completed",
            started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z",
        )
        r = lc_client.post("/api/study-sync-runs", json={"study": "sync-s"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_study_sync_runs_in_openapi(self, lc_client: TestClient) -> None:
        schema = lc_client.get("/openapi.json").json()
        assert "/api/study-sync-runs" in schema["paths"]

    # -----------------------------------------------------------------------
    # /api/proposed-input-decision
    # -----------------------------------------------------------------------

    _INV_YAML = """\
name: test-inv
proposed_inputs:
  items:
  - id: ref-a
    kind: reference
    citation: Smith 2024
    status: pending
inputs:
  references: []
"""

    def test_proposed_input_decision_missing_inv(self, lc_client: TestClient) -> None:
        r = lc_client.post(
            "/api/proposed-input-decision",
            json={"item_id": "ref-a", "decision": "accept"},
        )
        assert r.status_code == 400

    def test_proposed_input_decision_bad_decision(
        self, lc_client: TestClient, ws: Path
    ) -> None:
        inv = ws / "investigations" / "test-inv"
        inv.mkdir(parents=True)
        (inv / "investigation.yaml").write_text(self._INV_YAML)
        r = lc_client.post(
            "/api/proposed-input-decision",
            json={"investigation": "test-inv", "item_id": "ref-a", "decision": "nope"},
        )
        assert r.status_code == 400

    def test_proposed_input_decision_happy(self, lc_client: TestClient, ws: Path) -> None:
        import yaml

        inv = ws / "investigations" / "test-inv"
        inv.mkdir(parents=True)
        (inv / "investigation.yaml").write_text(self._INV_YAML)
        r = lc_client.post(
            "/api/proposed-input-decision",
            json={"investigation": "test-inv", "item_id": "ref-a", "decision": "accept"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body["status"] == "accepted"
        spec = yaml.safe_load((inv / "investigation.yaml").read_text())
        assert "ref-a" in spec["inputs"]["references"]

    def test_proposed_input_decision_in_openapi(self, lc_client: TestClient) -> None:
        schema = lc_client.get("/openapi.json").json()
        assert "/api/proposed-input-decision" in schema["paths"]

    # -----------------------------------------------------------------------
    # /api/study-seed-followup
    # -----------------------------------------------------------------------

    def test_study_seed_followup_no_parent(self, lc_client: TestClient) -> None:
        r = lc_client.post(
            "/api/study-seed-followup",
            json={"parent": "nope", "followup_idx": 0},
        )
        assert r.status_code in (400, 404)
        assert "error" in r.json()

    def test_study_seed_followup_happy(self, lc_client: TestClient, ws: Path) -> None:
        import yaml

        d = ws / "studies" / "parent"
        d.mkdir()
        (d / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 4, "name": "parent", "status": "ran",
            "baseline": [{"name": "b", "composite": "x"}],
            "follow_up_studies": [{"title": "child", "kind": "new", "why": "w"}],
        }, sort_keys=False))
        r = lc_client.post(
            "/api/study-seed-followup",
            json={"parent": "parent", "followup_idx": 0},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["new_study_name"]
        assert body["new_slug"] == body["new_study_name"]

    def test_study_seed_followup_in_openapi(self, lc_client: TestClient) -> None:
        schema = lc_client.get("/openapi.json").json()
        assert "/api/study-seed-followup" in schema["paths"]


# ---------------------------------------------------------------------------
# Batch 21: Investigation scaffold POST routes
# ---------------------------------------------------------------------------


class TestBatch21ScaffoldRoutes:
    """Route-level tests for Batch 21 investigation scaffold POST endpoints."""

    @pytest.fixture
    def ws(self, tmp_path: Path) -> Path:
        w = tmp_path / "ws"
        w.mkdir()
        (w / "workspace.yaml").write_text(
            "schema_version: 2\nname: ws\ncreated: '2026-01-01'\nplugin_version: 0.6.1\npackage_path: pkg\n"
        )
        (w / "investigations").mkdir()
        (w / "studies").mkdir()
        return w

    @pytest.fixture
    def sc_client(self, ws: Path) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        return TestClient(app)

    # -----------------------------------------------------------------------
    # /api/iset-create
    # -----------------------------------------------------------------------

    def test_iset_create_happy(self, sc_client: TestClient, ws: Path) -> None:
        r = sc_client.post("/api/iset-create", json={"name": "new-inv", "overview": "Test"})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "new-inv"
        assert body["status"] == "planning"
        assert (ws / "investigations" / "new-inv" / "investigation.yaml").is_file()

    def test_iset_create_missing_name(self, sc_client: TestClient) -> None:
        r = sc_client.post("/api/iset-create", json={})
        assert r.status_code == 400
        assert "error" in r.json()

    def test_iset_create_bad_slug(self, sc_client: TestClient) -> None:
        r = sc_client.post("/api/iset-create", json={"name": "BadSlug"})
        assert r.status_code == 400

    def test_iset_create_conflict(self, sc_client: TestClient) -> None:
        sc_client.post("/api/iset-create", json={"name": "dup"})
        r = sc_client.post("/api/iset-create", json={"name": "dup"})
        assert r.status_code == 409

    def test_iset_create_in_openapi(self, sc_client: TestClient) -> None:
        paths = sc_client.get("/openapi.json").json()["paths"]
        assert "/api/iset-create" in paths
        assert "post" in paths["/api/iset-create"]

    # -----------------------------------------------------------------------
    # /api/iset-clone
    # -----------------------------------------------------------------------

    _STUB_CLONE_SCRIPT = """\
#!/usr/bin/env python3
import argparse, json, sys, yaml
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--source', required=True)
p.add_argument('--target', required=True)
p.add_argument('--source-root', required=True, type=Path)
p.add_argument('--target-root', required=True, type=Path)
p.add_argument('--source-prefix', default=None)
p.add_argument('--target-prefix', default=None)
p.add_argument('--json', action='store_true')
a = p.parse_args()
src = a.source_root / 'investigations' / a.source / 'investigation.yaml'
dst_dir = a.target_root / 'investigations' / a.target
dst_dir.mkdir(parents=True, exist_ok=False)
spec = yaml.safe_load(src.read_text())
spec['name'] = a.target
(dst_dir / 'investigation.yaml').write_text(yaml.safe_dump(spec, sort_keys=False))
if a.json:
    print(json.dumps({'source': a.source, 'target': a.target, 'studies_remapped': {}}))
"""

    def _seed_src_inv(self, ws: Path) -> None:
        (ws / "scripts").mkdir(exist_ok=True)
        (ws / "scripts" / "clone_investigation.py").write_text(self._STUB_CLONE_SCRIPT)
        inv_dir = ws / "investigations" / "src-inv"
        inv_dir.mkdir(parents=True, exist_ok=True)
        (inv_dir / "investigation.yaml").write_text(
            "schema_version: 2\nname: src-inv\ntitle: src-inv\nstatus: planning\nstudies: []\n"
        )

    def test_iset_clone_missing_source_target(self, sc_client: TestClient) -> None:
        r = sc_client.post("/api/iset-clone", json={"source": "x"})
        assert r.status_code == 400
        assert "error" in r.json()

    def test_iset_clone_source_not_found(self, sc_client: TestClient) -> None:
        r = sc_client.post("/api/iset-clone", json={"source": "nope", "target": "dst"})
        assert r.status_code == 404

    def test_iset_clone_happy(self, sc_client: TestClient, ws: Path) -> None:
        self._seed_src_inv(ws)
        r = sc_client.post("/api/iset-clone", json={"source": "src-inv", "target": "dst-inv"})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "dst-inv"
        assert "clone_summary" in body

    def test_iset_clone_in_openapi(self, sc_client: TestClient) -> None:
        paths = sc_client.get("/openapi.json").json()["paths"]
        assert "/api/iset-clone" in paths
        assert "post" in paths["/api/iset-clone"]

    # -----------------------------------------------------------------------
    # /api/investigation-delete
    # -----------------------------------------------------------------------

    def test_investigation_delete_missing_name(self, sc_client: TestClient) -> None:
        r = sc_client.post("/api/investigation-delete", json={})
        assert r.status_code == 400
        assert "error" in r.json()

    def test_investigation_delete_not_found(self, sc_client: TestClient) -> None:
        r = sc_client.post("/api/investigation-delete", json={"name": "ghost"})
        assert r.status_code == 404

    def test_investigation_delete_happy(self, sc_client: TestClient, ws: Path) -> None:
        inv_dir = ws / "investigations" / "bye-inv"
        inv_dir.mkdir(parents=True)
        (inv_dir / "investigation.yaml").write_text(
            "name: bye-inv\nstatus: planning\nstudies: []\n"
        )
        r = sc_client.post("/api/investigation-delete", json={"name": "bye-inv"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["name"] == "bye-inv"
        assert not inv_dir.exists()

    def test_investigation_delete_in_openapi(self, sc_client: TestClient) -> None:
        paths = sc_client.get("/openapi.json").json()["paths"]
        assert "/api/investigation-delete" in paths
        assert "post" in paths["/api/investigation-delete"]


class TestBatch26ReferenceRoutes:
    """Route-level tests for Batch 26 reference POST endpoints
    (/api/reference-pdf, /api/reference-bibtex, /api/reference)."""

    _INV_SLUG = "dnaa-replication"

    @pytest.fixture
    def ws(self, tmp_path: Path, monkeypatch) -> Path:
        import pbg_superpowers
        schema_src = (Path(pbg_superpowers.__file__).parent / "schemas"
                      / "workspace.schema.json")
        w = tmp_path / "ws"
        w.mkdir()
        (w / "workspace.yaml").write_text(
            "schema_version: 3\nname: testws\ncreated: '2026-01-01'\n"
            "plugin_version: '0.14.0'\npackage_path: pbg_testws\n"
            "datasets: []\nexpert_docs: []\nimports: {}\n",
            encoding="utf-8",
        )
        schemas = w / ".pbg" / "schemas"
        schemas.mkdir(parents=True)
        (schemas / "workspace.schema.json").write_text(
            schema_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        inv = w / "investigations" / self._INV_SLUG
        (inv / "studies").mkdir(parents=True)
        (inv / "investigation.yaml").write_text(
            f"name: {self._INV_SLUG}\ntitle: {self._INV_SLUG}\nstudies: []\n",
            encoding="utf-8",
        )
        # Register the root so load_workspace/save_workspace resolve the schema.
        import vivarium_dashboard.lib._root as _root
        monkeypatch.setattr(_root, "_WS_ROOT", w.resolve())
        monkeypatch.setattr(_root, "_WS_PATHS", None)
        return w

    @pytest.fixture
    def rc(self, ws: Path) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        return TestClient(app)

    @staticmethod
    def _pdf() -> str:
        import base64
        return base64.b64encode(b"%PDF-1.4 not a real pdf").decode()

    # -- /api/reference-pdf --------------------------------------------------

    def test_reference_pdf_happy(self, rc: TestClient, ws: Path) -> None:
        r = rc.post("/api/reference-pdf", json={
            "pdf_b64": self._pdf(), "title": "T", "authors": "A B",
            "year": 2021, "bib_key": "AB2021",
        })
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["ok"] is True
        assert body["bib_key"] == "AB2021"
        assert body["metadata_pending"] is False
        assert "extracted" in body
        assert (ws / "references" / "papers" / "AB2021.pdf").is_file()

    def test_reference_pdf_400_missing_pdf(self, rc: TestClient) -> None:
        r = rc.post("/api/reference-pdf", json={"title": "T"})
        assert r.status_code == 400
        assert "pdf_b64 is required" in r.json().get("error", "")

    def test_reference_pdf_409_duplicate(self, rc: TestClient) -> None:
        body = {"pdf_b64": self._pdf(), "title": "T", "authors": "A B",
                "year": 2021, "bib_key": "Dup2021"}
        assert rc.post("/api/reference-pdf", json=body).status_code == 200
        r = rc.post("/api/reference-pdf", json=body)
        assert r.status_code == 409
        assert "already exists" in r.json().get("error", "")

    def test_reference_pdf_404_investigation(self, rc: TestClient) -> None:
        r = rc.post("/api/reference-pdf", json={
            "pdf_b64": self._pdf(), "title": "T", "authors": "A B", "year": 2021,
            "bib_key": "AB2021", "investigation": "ghost-inv",
        })
        assert r.status_code == 404

    def test_reference_pdf_in_openapi(self, rc: TestClient) -> None:
        paths = rc.get("/openapi.json").json()["paths"]
        assert "/api/reference-pdf" in paths and "post" in paths["/api/reference-pdf"]

    # -- /api/reference-bibtex + /api/reference (alias) ----------------------

    def test_reference_bibtex_happy(self, rc: TestClient, ws: Path) -> None:
        r = rc.post("/api/reference-bibtex", json={
            "bibtex_text": "@article{Foo2020, year = {2020}}",
        })
        assert r.status_code == 200, r.json()
        assert r.json()["ok"] is True
        assert "Foo2020" in (ws / "references" / "papers.bib").read_text()

    def test_reference_alias_happy(self, rc: TestClient, ws: Path) -> None:
        r = rc.post("/api/reference", json={
            "bibtex_text": "@article{Bar2021, year = {2021}}",
        })
        assert r.status_code == 200, r.json()
        assert r.json()["ok"] is True
        assert "Bar2021" in (ws / "references" / "papers.bib").read_text()

    def test_reference_bibtex_and_alias_behave_identically(
        self, rc: TestClient, ws: Path
    ) -> None:
        # Same key via both routes: first succeeds, the second (global, dup) 409s
        # regardless of which path is hit — proving identical handling.
        r1 = rc.post("/api/reference-bibtex", json={
            "bibtex_text": "@article{Same2020, year = {2020}}",
        })
        assert r1.status_code == 200
        r2 = rc.post("/api/reference", json={
            "bibtex_text": "@article{Same2020, year = {2020}}",
        })
        assert r2.status_code == 409
        # And the reverse ordering for a fresh key:
        r3 = rc.post("/api/reference", json={
            "bibtex_text": "@article{Other2020, year = {2020}}",
        })
        assert r3.status_code == 200
        r4 = rc.post("/api/reference-bibtex", json={
            "bibtex_text": "@article{Other2020, year = {2020}}",
        })
        assert r4.status_code == 409

    def test_reference_bibtex_400_missing_text(self, rc: TestClient) -> None:
        r = rc.post("/api/reference-bibtex", json={})
        assert r.status_code == 400
        assert "bibtex_text is required" in r.json().get("error", "")

    def test_reference_bibtex_400_unparseable(self, rc: TestClient) -> None:
        r = rc.post("/api/reference-bibtex", json={"bibtex_text": "no key"})
        assert r.status_code == 400
        assert "could not parse BibTeX key" in r.json().get("error", "")

    def test_reference_routes_in_openapi(self, rc: TestClient) -> None:
        paths = rc.get("/openapi.json").json()["paths"]
        assert "/api/reference-bibtex" in paths and "post" in paths["/api/reference-bibtex"]
        assert "/api/reference" in paths and "post" in paths["/api/reference"]


class TestBatch27CompositeRoutes:
    """Route-level tests for Batch 27 composite POST endpoints
    (/api/investigation-composite-add, -perturb, -rebuild,
    /api/composite-promote-to-catalog)."""

    _INV = "demo"

    @pytest.fixture
    def ws(self, tmp_path: Path) -> Path:
        import yaml as _yaml
        w = tmp_path / "ws"
        w.mkdir()
        (w / "workspace.yaml").write_text(
            "schema_version: 3\nname: testws\npackage_path: pbg_testws\n",
            encoding="utf-8",
        )
        inv = w / "investigations" / self._INV
        (inv / "composites").mkdir(parents=True)
        (inv / "spec.yaml").write_text(
            _yaml.safe_dump({"name": self._INV, "composites": [], "variants": []}),
            encoding="utf-8",
        )
        # A real YAML composite source + a parent sidecar for perturb/rebuild.
        cdir = w / "pbg_testws" / "composites"
        cdir.mkdir(parents=True)
        (cdir / "baseline.composite.yaml").write_text(
            _yaml.safe_dump({"name": "baseline-doc", "state": {},
                             "parameters": {"rate": {"default": 1.0}}}),
            encoding="utf-8",
        )
        (inv / "composites" / "baseline.yaml").write_text(
            _yaml.safe_dump({"name": "baseline-doc", "state": {},
                             "parameters": {"rate": {"default": 1.0}}}),
            encoding="utf-8",
        )
        return w

    @pytest.fixture
    def rc(self, ws: Path) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        return TestClient(app)

    # -- add -----------------------------------------------------------------

    def test_add_happy(self, rc: TestClient, ws: Path) -> None:
        r = rc.post("/api/investigation-composite-add", json={
            "investigation": self._INV, "name": "base",
            "source": "pbg_testws.composites.baseline",
        })
        assert r.status_code == 200, r.json()
        assert r.json()["ok"] is True
        assert (ws / "investigations" / self._INV / "composites" / "base.yaml").is_file()

    def test_add_400_missing(self, rc: TestClient) -> None:
        r = rc.post("/api/investigation-composite-add", json={"investigation": self._INV})
        assert r.status_code == 400
        assert "required" in r.json()["error"]

    def test_add_404_unknown_source(self, rc: TestClient) -> None:
        r = rc.post("/api/investigation-composite-add", json={
            "investigation": self._INV, "name": "x",
            "source": "pbg_testws.composites.nope",
        })
        assert r.status_code == 404

    def test_add_409_duplicate(self, rc: TestClient) -> None:
        body = {"investigation": self._INV, "name": "base",
                "source": "pbg_testws.composites.baseline"}
        assert rc.post("/api/investigation-composite-add", json=body).status_code == 200
        r = rc.post("/api/investigation-composite-add", json=body)
        assert r.status_code == 409

    # -- perturb -------------------------------------------------------------

    def test_perturb_happy(self, rc: TestClient, ws: Path) -> None:
        r = rc.post("/api/investigation-composite-perturb", json={
            "investigation": self._INV, "name": "fast", "extends": "baseline",
            "parameter_overrides": {"rate": 2.0},
        })
        assert r.status_code == 200, r.json()
        import yaml as _yaml
        derived = ws / "investigations" / self._INV / "composites" / "fast.yaml"
        assert _yaml.safe_load(derived.read_text())["parameters"]["rate"]["default"] == 2.0

    def test_perturb_404_parent(self, rc: TestClient) -> None:
        r = rc.post("/api/investigation-composite-perturb", json={
            "investigation": self._INV, "name": "x", "extends": "nope",
        })
        assert r.status_code == 404

    # -- promote -------------------------------------------------------------

    def test_promote_happy_with_augmentation(self, rc: TestClient, ws: Path) -> None:
        # Seed a variant sidecar + spec entry.
        import yaml as _yaml
        inv = ws / "investigations" / self._INV
        (inv / "composites" / "myvar.yaml").write_text(
            _yaml.safe_dump({"name": "myvar-doc", "state": {}}), encoding="utf-8")
        (inv / "spec.yaml").write_text(
            _yaml.safe_dump({"name": self._INV, "variants": [{"name": "myvar"}]}),
            encoding="utf-8")
        r = rc.post("/api/composite-promote-to-catalog", json={
            "investigation": self._INV, "variant": "myvar", "target_name": "pp",
        })
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["name"] == "pp"
        assert body["path"] == "pbg_testws/composites/pp.composite.yaml"
        assert (ws / "pbg_testws" / "composites" / "pp.composite.yaml").is_file()

    def test_promote_404_variant(self, rc: TestClient) -> None:
        r = rc.post("/api/composite-promote-to-catalog", json={
            "investigation": self._INV, "variant": "ghost",
        })
        assert r.status_code == 404

    # -- rebuild -------------------------------------------------------------

    def test_rebuild_happy(self, rc: TestClient, ws: Path) -> None:
        import yaml as _yaml
        inv = ws / "investigations" / self._INV
        (inv / "spec.yaml").write_text(_yaml.safe_dump({
            "name": self._INV, "composites": [
                {"name": "d", "extends": "baseline",
                 "parameter_overrides": {"rate": 7.0}},
            ],
        }), encoding="utf-8")
        r = rc.post("/api/investigation-composite-rebuild", json={
            "investigation": self._INV, "name": "d",
        })
        assert r.status_code == 200, r.json()
        derived = inv / "composites" / "d.yaml"
        assert _yaml.safe_load(derived.read_text())["parameters"]["rate"]["default"] == 7.0

    def test_rebuild_400_not_derived(self, rc: TestClient, ws: Path) -> None:
        import yaml as _yaml
        inv = ws / "investigations" / self._INV
        (inv / "spec.yaml").write_text(_yaml.safe_dump({
            "name": self._INV, "composites": [{"name": "flat"}],
        }), encoding="utf-8")
        r = rc.post("/api/investigation-composite-rebuild", json={
            "investigation": self._INV, "name": "flat",
        })
        assert r.status_code == 400
        assert "is not derived" in r.json()["error"]

    # -- openapi -------------------------------------------------------------

    def test_routes_in_openapi(self, rc: TestClient) -> None:
        paths = rc.get("/openapi.json").json()["paths"]
        for p in (
            "/api/investigation-composite-add",
            "/api/investigation-composite-perturb",
            "/api/composite-promote-to-catalog",
            "/api/investigation-composite-rebuild",
        ):
            assert p in paths and "post" in paths[p], p


class TestBatch28InvVizRoutes:
    """Route-level tests for Batch 28 investigation composite/viz POST endpoints
    (/api/investigation-create-from-composite, -add-viz, -render-viz)."""

    _INV = "demo"

    @pytest.fixture
    def ws(self, tmp_path: Path) -> Path:
        import yaml as _yaml
        w = tmp_path / "ws"
        w.mkdir()
        (w / "workspace.yaml").write_text(
            "schema_version: 3\nname: testws\npackage_path: pbg_testws\n",
            encoding="utf-8",
        )
        inv = w / "investigations" / self._INV
        inv.mkdir(parents=True)
        (inv / "spec.yaml").write_text(
            _yaml.safe_dump({"name": self._INV}), encoding="utf-8",
        )
        return w

    @pytest.fixture
    def rc(self, ws: Path, monkeypatch) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        return TestClient(app)

    # -- create-from-composite ----------------------------------------------

    def test_create_happy(self, rc: TestClient, ws: Path, monkeypatch) -> None:
        import types
        import yaml as _yaml
        from vivarium_dashboard.lib import composite_lookup as _clookup
        from vivarium_dashboard.lib import investigation_migrate as _imig
        from vivarium_dashboard.lib import composite_mutations as _cm

        cdir = ws / "pbg_testws" / "composites"
        cdir.mkdir(parents=True)
        src = cdir / "chromo.composite.yaml"
        src.write_text(_yaml.safe_dump({"name": "chromo-doc", "state": {}}), encoding="utf-8")
        ref = "pbg_testws.composites.chromo"
        monkeypatch.setattr(_clookup, "discover_all_composites", lambda root, pkg: {
            ref: {"name": "chromo", "id": ref, "kind": "spec", "_path": str(src)},
        })
        monkeypatch.setattr(_imig, "_resolve_composite_source", lambda r, root: (src, "chromo"))
        monkeypatch.setattr(_cm.uuid, "uuid4", lambda: types.SimpleNamespace(hex="abcdef000000"))

        r = rc.post("/api/investigation-create-from-composite", json={"composite_name": "chromo"})
        assert r.status_code == 200, r.json()
        assert r.json() == {"name": "study-chromo-abcdef"}
        assert (ws / "studies" / "study-chromo-abcdef" / "spec.yaml").is_file()

    def test_create_400_blank(self, rc: TestClient) -> None:
        r = rc.post("/api/investigation-create-from-composite", json={"composite_name": ""})
        assert r.status_code == 400
        assert r.json()["error"] == "composite_name required"

    def test_create_404_not_in_catalog(self, rc: TestClient, monkeypatch) -> None:
        from vivarium_dashboard.lib import composite_lookup as _clookup
        monkeypatch.setattr(_clookup, "discover_all_composites", lambda root, pkg: {})
        r = rc.post("/api/investigation-create-from-composite", json={"composite_name": "ghost"})
        assert r.status_code == 404
        assert "not in workspace catalog" in r.json()["error"]

    # -- add-viz -------------------------------------------------------------

    def test_add_viz_happy(self, rc: TestClient, ws: Path) -> None:
        import yaml as _yaml
        r = rc.post("/api/investigation-add-viz", json={
            "investigation": self._INV, "name": "my-plot",
            "address": "local:TimeSeriesPlot", "config": {"x": "time"},
        })
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body == {"ok": True, "investigation": self._INV, "viz_name": "my-plot"}
        spec = _yaml.safe_load((ws / "investigations" / self._INV / "spec.yaml").read_text())
        assert spec["visualizations"][0]["name"] == "my-plot"

    def test_add_viz_400_bad_name(self, rc: TestClient) -> None:
        r = rc.post("/api/investigation-add-viz", json={
            "investigation": self._INV, "name": "bad name!", "address": "local:X",
        })
        assert r.status_code == 400
        assert r.json()["error"] == "viz name must match [a-zA-Z0-9_-]+"

    def test_add_viz_404(self, rc: TestClient) -> None:
        r = rc.post("/api/investigation-add-viz", json={
            "investigation": "ghost", "name": "p", "address": "local:X",
        })
        assert r.status_code == 404

    def test_add_viz_409_duplicate(self, rc: TestClient, ws: Path) -> None:
        import yaml as _yaml
        inv = ws / "investigations" / self._INV
        (inv / "spec.yaml").write_text(_yaml.safe_dump({
            "name": self._INV,
            "visualizations": [{"name": "p", "address": "local:X", "config": {}}],
        }), encoding="utf-8")
        r = rc.post("/api/investigation-add-viz", json={
            "investigation": self._INV, "name": "p", "address": "local:Y",
        })
        assert r.status_code == 409
        assert "already exists in spec" in r.json()["error"]

    # -- render-viz ----------------------------------------------------------

    def test_render_viz_400_name_required(self, rc: TestClient) -> None:
        r = rc.post("/api/investigation-render-viz", json={"name": ""})
        assert r.status_code == 400
        assert r.json()["error"] == "name is required"

    def test_render_viz_404(self, rc: TestClient) -> None:
        r = rc.post("/api/investigation-render-viz", json={"name": "ghost"})
        assert r.status_code == 404

    # -- openapi -------------------------------------------------------------

    def test_routes_in_openapi(self, rc: TestClient) -> None:
        paths = rc.get("/openapi.json").json()["paths"]
        for p in (
            "/api/investigation-create-from-composite",
            "/api/investigation-add-viz",
            "/api/investigation-render-viz",
        ):
            assert p in paths and "post" in paths[p], p


# ===========================================================================
# Job status routes (in-memory manager singletons, read-only)
# ===========================================================================

class _FakeJob:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


class _FakeManager:
    def __init__(self, jobs=None, recent=None) -> None:
        self._jobs = jobs or {}
        self._recent = recent if recent is not None else []

    def list_recent(self, n: int = 20):
        return list(self._recent)

    def get(self, job_id: str):
        return self._jobs.get(job_id)


class TestJobStatusRoutes:
    """The two FastAPI job-status GETs read the manager at call time via the
    module attribute, so monkeypatching ``<module>.manager`` reroutes them to a
    fake — no real background threads."""

    # -- investigation-run-unblocked-status (lib.run_jobs.manager) -----------

    def test_run_unblocked_jobs_list_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import run_jobs
        monkeypatch.setattr(run_jobs, "manager", _FakeManager(recent=[{"job_id": "r1"}]))
        r = client.get("/api/investigation-run-unblocked-status")
        assert r.status_code == 200
        assert r.json() == {"jobs": [{"job_id": "r1"}]}

    def test_run_unblocked_single_job_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import run_jobs
        job = _FakeJob({"job_id": "r9", "items": [{"status": "running"}]})
        monkeypatch.setattr(run_jobs, "manager", _FakeManager(jobs={"r9": job}))
        r = client.get("/api/investigation-run-unblocked-status?job_id=r9")
        assert r.status_code == 200
        assert r.json() == {"job_id": "r9", "items": [{"status": "running"}]}

    def test_run_unblocked_missing_404(self, client, monkeypatch):
        from vivarium_dashboard.lib import run_jobs
        monkeypatch.setattr(run_jobs, "manager", _FakeManager(jobs={}))
        r = client.get("/api/investigation-run-unblocked-status?job_id=ghost")
        assert r.status_code == 404
        assert r.json() == {"error": "job not found"}

    # -- remote-run-status (lib.remote_run_jobs.manager) ---------------------

    def test_remote_run_jobs_list_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import remote_run_jobs
        monkeypatch.setattr(remote_run_jobs, "manager", _FakeManager(recent=[{"job_id": "rr1"}]))
        r = client.get("/api/remote-run-status")
        assert r.status_code == 200
        assert r.json() == {"jobs": [{"job_id": "rr1"}]}

    def test_remote_run_single_job_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import remote_run_jobs
        job = _FakeJob({"job_id": "rr9", "steps": [{"name": "fetch", "status": "done"}]})
        monkeypatch.setattr(remote_run_jobs, "manager", _FakeManager(jobs={"rr9": job}))
        r = client.get("/api/remote-run-status?job_id=rr9")
        assert r.status_code == 200
        assert r.json() == {"job_id": "rr9", "steps": [{"name": "fetch", "status": "done"}]}

    def test_remote_run_missing_404(self, client, monkeypatch):
        from vivarium_dashboard.lib import remote_run_jobs
        monkeypatch.setattr(remote_run_jobs, "manager", _FakeManager(jobs={}))
        r = client.get("/api/remote-run-status?job_id=ghost")
        assert r.status_code == 404
        assert r.json() == {"error": "job not found"}

    def test_routes_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        for p in ("/api/investigation-run-unblocked-status", "/api/remote-run-status"):
            assert p in paths and "get" in paths[p], p
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        assert "JobStatusPayload" in schemas


# ===========================================================================
# C-state-3a: CSRF same-origin middleware (whole POST/DELETE surface)
# ===========================================================================
class TestCsrfMiddleware:
    """The @app.middleware('http') guard rejects cross-origin POST/DELETE and
    never blocks GET. starlette's TestClient sends Host='testserver' and no
    Origin by default (existing POST tests stay green)."""

    def test_cross_origin_post_403(self, client):
        # Existing POST route; cross-origin Origin → blocked BEFORE the route.
        r = client.post(
            "/api/source/switch",
            json={"path": "/whatever"},
            headers={"Origin": "http://evil.example.com"},
        )
        assert r.status_code == 403
        assert r.json() == {"error": "cross-origin request forbidden"}

    def test_same_origin_post_passes(self, client, monkeypatch):
        # Origin host == Host ('testserver') → reaches the route (here a 400,
        # not a 403 — it passed the guard).
        from pbg_superpowers import workspace_catalog
        monkeypatch.setattr(workspace_catalog, "list_workspaces", lambda: [])
        r = client.post(
            "/api/source/switch",
            json={"path": "/nope"},
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 400
        assert r.json()["error"].endswith("is not a registered workspace")

    def test_no_origin_post_passes(self, client, monkeypatch):
        from pbg_superpowers import workspace_catalog
        monkeypatch.setattr(workspace_catalog, "list_workspaces", lambda: [])
        r = client.post("/api/source/switch", json={"path": "/nope"})
        assert r.status_code != 403
        assert r.status_code == 400

    def test_env_disabled_post_passes(self, client, monkeypatch):
        monkeypatch.setenv("VIVARIUM_DASHBOARD_DISABLE_CSRF", "1")
        from pbg_superpowers import workspace_catalog
        monkeypatch.setattr(workspace_catalog, "list_workspaces", lambda: [])
        r = client.post(
            "/api/source/switch",
            json={"path": "/nope"},
            headers={"Origin": "http://evil.example.com"},
        )
        assert r.status_code != 403
        assert r.status_code == 400

    def test_get_never_blocked(self, client):
        # A cross-origin GET is never blocked by the CSRF middleware.
        r = client.get("/health", headers={"Origin": "http://evil.example.com"})
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ===========================================================================
# C-state-3a: POST /api/source/switch (in-process workspace re-point)
# ===========================================================================
class TestSourceSwitchRoute:
    def test_missing_path_400(self, client):
        r = client.post("/api/source/switch", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "missing 'path'"}

    def test_unregistered_path_400(self, client, tmp_path, monkeypatch):
        from pbg_superpowers import workspace_catalog
        monkeypatch.setattr(workspace_catalog, "list_workspaces", lambda: [])
        p = str(tmp_path / "nope")
        r = client.post("/api/source/switch", json={"path": p})
        assert r.status_code == 400
        assert r.json() == {"error": f"{p!r} is not a registered workspace"}

    def test_happy_path_repoints(self, client, tmp_path, monkeypatch):
        from pbg_superpowers import workspace_catalog
        from vivarium_dashboard.lib import _root
        ws = tmp_path / "ws2"
        ws.mkdir()
        (ws / "workspace.yaml").write_text("name: w2\n")
        monkeypatch.setattr(
            workspace_catalog, "list_workspaces",
            lambda: [{"path": str(ws), "name": "w2"}],
        )
        r = client.post("/api/source/switch", json={"path": str(ws)})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "source": {"path": str(ws), "name": "w2"}}
        # The route re-pointed the shared lib root (autouse fixture resets it).
        assert _root.get_workspace_root() == ws.resolve()

    def test_route_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/source/switch" in paths and "post" in paths["/api/source/switch"]
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        assert "SourceSwitchResponse" in schemas


# ===========================================================================
# C-state-3b: POST /api/source/build-remote + /api/source/switch-build
# (sms-api NETWORK routes — every test monkeypatches the lib sms-api names)
# ===========================================================================
class TestSourceBuildRemoteRoute:
    def test_missing_repo_branch_400(self, client):
        r = client.post("/api/source/build-remote", json={"repo": "x"})
        assert r.status_code == 400
        assert r.json() == {"error": "repo and branch are required"}

    def test_no_commit_502(self, client, monkeypatch):
        from vivarium_dashboard.lib import source_build_views as sbv

        class _Client:
            def __init__(self, base=None):
                pass

            def latest_simulator(self, repo, branch):
                return {"git_commit_hash": ""}

        monkeypatch.setattr(sbv, "SmsApiClient", _Client)
        r = client.post("/api/source/build-remote", json={"repo": "r", "branch": "b"})
        assert r.status_code == 502
        assert r.json() == {"error": "could not resolve branch HEAD via sms-api"}

    def test_sms_api_error_502(self, client, monkeypatch):
        from vivarium_dashboard.lib import source_build_views as sbv
        from vivarium_dashboard.lib.sms_api_client import SmsApiError

        class _Client:
            def __init__(self, base=None):
                pass

            def latest_simulator(self, repo, branch):
                raise SmsApiError("boom")

        monkeypatch.setattr(sbv, "SmsApiClient", _Client)
        r = client.post("/api/source/build-remote", json={"repo": "r", "branch": "b"})
        assert r.status_code == 502
        assert r.json() == {"error": "sms-api: boom"}

    def test_happy_path(self, client, monkeypatch):
        from vivarium_dashboard.lib import source_build_views as sbv

        class _Client:
            def __init__(self, base=None):
                pass

            def latest_simulator(self, repo, branch):
                return {"git_commit_hash": "c0ffee"}

            def register_simulator(self, repo, branch, commit):
                return {"database_id": 42}

        monkeypatch.setattr(sbv, "SmsApiClient", _Client)
        r = client.post(
            "/api/source/build-remote",
            json={"repo": "https://github.com/x/y.git", "branch": "main"},
        )
        assert r.status_code == 200
        assert r.json() == {
            "ok": True, "simulator_id": 42,
            "repo": "https://github.com/x/y", "branch": "main", "commit": "c0ffee",
        }

    def test_route_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/source/build-remote" in paths
        assert "post" in paths["/api/source/build-remote"]
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        assert "BuildRemoteResponse" in schemas


class TestSourceSwitchBuildRoute:
    @staticmethod
    def _entry(sim_id=5):
        return {
            "simulator_id": sim_id, "repo": "y",
            "repo_url": "https://github.com/x/y", "commit": "deadbeef",
            "branch": "main", "label": "y @ deadbeef (build #5)",
        }

    def test_missing_sim_id_400(self, client):
        r = client.post("/api/source/switch-build", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "missing 'simulator_id'"}

    def test_listing_error_502(self, client, monkeypatch):
        from vivarium_dashboard.lib import source_build_views as sbv
        monkeypatch.setattr(sbv, "SmsApiClient", lambda base=None: object())
        monkeypatch.setattr(
            sbv, "list_build_sources",
            lambda c: {"builds": [], "error": "tunnel down"},
        )
        r = client.post("/api/source/switch-build", json={"simulator_id": 5})
        assert r.status_code == 502
        assert r.json() == {"error": "sms-api unavailable: tunnel down"}

    def test_not_found_404(self, client, monkeypatch):
        from vivarium_dashboard.lib import source_build_views as sbv
        monkeypatch.setattr(sbv, "SmsApiClient", lambda base=None: object())
        monkeypatch.setattr(
            sbv, "list_build_sources", lambda c: {"builds": [self._entry(99)]},
        )
        r = client.post("/api/source/switch-build", json={"simulator_id": 5})
        assert r.status_code == 404
        assert r.json() == {"error": "build 5 not found"}

    def test_materialize_error_502(self, client, monkeypatch):
        from vivarium_dashboard.lib import source_build_views as sbv
        from vivarium_dashboard.lib.sms_api_client import SmsApiError
        monkeypatch.setattr(sbv, "SmsApiClient", lambda base=None: object())
        monkeypatch.setattr(
            sbv, "list_build_sources", lambda c: {"builds": [self._entry(5)]},
        )

        def _boom(c, sim_id, commit):
            raise SmsApiError("no tarball")

        monkeypatch.setattr(sbv, "materialize_build", _boom)
        r = client.post("/api/source/switch-build", json={"simulator_id": 5})
        assert r.status_code == 502
        assert r.json() == {"error": "materialize failed: no tarball"}

    def test_happy_path_repoints(self, client, tmp_path, monkeypatch):
        from vivarium_dashboard.lib import source_build_views as sbv
        from vivarium_dashboard.lib import _root
        cache = tmp_path / "cache"
        cache.mkdir()
        monkeypatch.setattr(sbv, "SmsApiClient", lambda base=None: object())
        monkeypatch.setattr(
            sbv, "list_build_sources", lambda c: {"builds": [self._entry(5)]},
        )
        monkeypatch.setattr(
            sbv, "materialize_build", lambda c, sim_id, commit: cache,
        )
        r = client.post("/api/source/switch-build", json={"simulator_id": 5})
        assert r.status_code == 200
        assert r.json() == {
            "ok": True,
            "source": {"path": str(cache), "name": "y @ deadbeef (build #5)"},
        }
        # The route re-pointed the shared lib root (autouse fixture resets it).
        assert _root.get_workspace_root() == cache.resolve()

    def test_route_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/source/switch-build" in paths
        assert "post" in paths["/api/source/switch-build"]


# ===========================================================================
# C-state-3c: POST /api/remote-run-start (manager.submit pipeline job)
# Every external (auth, git, sms-api, manager) is monkeypatched on the lib
# builder — no real network/git/auth.
# ===========================================================================
class TestRemoteRunStartRoute:
    def test_not_authenticated_401(self, client, monkeypatch):
        from vivarium_dashboard.lib import remote_run_views as rrv
        monkeypatch.setattr(rrv.github_auth, "current_session", lambda: None)
        r = client.post("/api/remote-run-start", json={"study": "s"})
        assert r.status_code == 401
        assert r.json() == {"error": "not authenticated"}

    def test_missing_study_400(self, client, monkeypatch):
        from vivarium_dashboard.lib import remote_run_views as rrv
        monkeypatch.setattr(rrv.github_auth, "current_session", lambda: object())
        r = client.post("/api/remote-run-start", json={"study": "   "})
        assert r.status_code == 400
        assert r.json() == {"error": "study is required"}

    def test_happy_path_202(self, client, tmp_path, monkeypatch):
        from vivarium_dashboard.lib import remote_run_views as rrv

        class _Job:
            job_id = "JX"

        captured = {}

        monkeypatch.setattr(rrv.github_auth, "current_session", lambda: object())
        monkeypatch.setattr(rrv.git_status, "has_origin_remote", lambda ws: True)
        monkeypatch.setattr(rrv.git_status, "remote_repo_url", lambda ws: "https://github.com/x/y")
        spec_file = tmp_path / "study.yaml"
        spec_file.write_text("baseline: []\n")
        monkeypatch.setattr(rrv.study_spec, "study_spec_path", lambda ws, name: spec_file)
        monkeypatch.setattr(rrv.study_spec, "study_dir", lambda ws, name: tmp_path)
        monkeypatch.setattr(rrv, "load_spec", lambda p: {"baseline": [], "readouts": []})
        import subprocess as _sp
        monkeypatch.setattr(
            rrv.subprocess, "run",
            lambda *a, **k: _sp.CompletedProcess(args=[], returncode=0, stdout="feature/x\n"),
        )
        monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: object())
        monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")

        def _submit(study, worker_fn):
            captured["study"] = study
            captured["worker"] = worker_fn
            return _Job()

        monkeypatch.setattr(rrv.manager, "submit", _submit)

        r = client.post("/api/remote-run-start", json={"study": "study-a"})
        assert r.status_code == 202
        assert r.json() == {"job_id": "JX"}
        assert captured["study"] == "study-a"
        assert callable(captured["worker"])

    def _wire_happy(self, rrv, tmp_path, monkeypatch, captured):
        """Monkeypatch every external for a happy submit; capture PipelineCtx kwargs."""
        class _Job:
            job_id = "JX"
        monkeypatch.setattr(rrv.github_auth, "current_session", lambda: object())
        monkeypatch.setattr(rrv.git_status, "has_origin_remote", lambda ws: True)
        monkeypatch.setattr(rrv.git_status, "remote_repo_url", lambda ws: "https://github.com/x/y")
        spec_file = tmp_path / "study.yaml"
        spec_file.write_text("baseline: []\n")
        monkeypatch.setattr(rrv.study_spec, "study_spec_path", lambda ws, name: spec_file)
        monkeypatch.setattr(rrv.study_spec, "study_dir", lambda ws, name: tmp_path)
        monkeypatch.setattr(rrv, "load_spec", lambda p: {"baseline": [], "readouts": []})
        import subprocess as _sp
        monkeypatch.setattr(
            rrv.subprocess, "run",
            lambda *a, **k: _sp.CompletedProcess(args=[], returncode=0, stdout="feature/x\n"),
        )
        monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: object())
        monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")

        def _ctx(**kwargs):
            captured.update(kwargs)
            return object()
        monkeypatch.setattr(rrv, "PipelineCtx", _ctx)
        monkeypatch.setattr(rrv, "run_remote_pipeline", lambda j, ctx: None)
        monkeypatch.setattr(rrv.manager, "submit", lambda study, worker_fn: _Job())

    def test_run_parca_defaults_true_when_omitted(self, client, tmp_path, monkeypatch):
        # Legacy raw-JSON contract: an OMITTED run_parca runs ParCa (.get(..., True)).
        # The route must not let pydantic's None default flip it to False.
        from vivarium_dashboard.lib import remote_run_views as rrv
        captured = {}
        self._wire_happy(rrv, tmp_path, monkeypatch, captured)
        r = client.post("/api/remote-run-start", json={"study": "study-a"})
        assert r.status_code == 202
        assert captured["run_parca"] is True

    def test_run_parca_explicit_false_preserved(self, client, tmp_path, monkeypatch):
        from vivarium_dashboard.lib import remote_run_views as rrv
        captured = {}
        self._wire_happy(rrv, tmp_path, monkeypatch, captured)
        r = client.post("/api/remote-run-start", json={"study": "study-a", "run_parca": False})
        assert r.status_code == 202
        assert captured["run_parca"] is False

    def test_route_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/remote-run-start" in paths
        assert "post" in paths["/api/remote-run-start"]
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        assert "RemoteRunStartResponse" in schemas


# ===========================================================================
# C-state-3e: GitHub device-flow auth (5 thin wrappers over lib.github_auth)
# Every test monkeypatches the github_auth fns reached via the auth_views
# module attribute — no test ever touches real GitHub.  The 2 POSTs pass CSRF
# because the TestClient sends no Origin header.
# ===========================================================================
class TestAuthRoutes:
    # -- POST /api/auth/github/start -----------------------------------------

    def test_start_no_client_id_503(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        monkeypatch.setattr(
            av.github_auth, "start_device_flow",
            lambda: {"error": "no_client_id", "hint": "set env"},
        )
        r = client.post("/api/auth/github/start", json={})
        assert r.status_code == 503
        assert r.json() == {"error": "no_client_id", "hint": "set env"}

    def test_start_other_error_502(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        monkeypatch.setattr(
            av.github_auth, "start_device_flow",
            lambda: {"error": "device_code_failed"},
        )
        r = client.post("/api/auth/github/start", json={})
        assert r.status_code == 502
        assert r.json() == {"error": "device_code_failed"}

    def test_start_success_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        payload = {
            "flow_id": "abc", "user_code": "WXYZ-1234",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900, "interval": 5,
        }
        monkeypatch.setattr(av.github_auth, "start_device_flow", lambda: payload)
        # no JSON body at all → permissive Body(default={}) is used
        r = client.post("/api/auth/github/start")
        assert r.status_code == 200
        assert r.json() == payload

    # -- GET /api/auth/github/poll -------------------------------------------

    def test_poll_missing_flow_id_400(self, client):
        r = client.get("/api/auth/github/poll")
        assert r.status_code == 400
        assert r.json() == {"status": "error", "detail": "missing_flow_id"}

    def test_poll_pending_202(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        monkeypatch.setattr(
            av.github_auth, "poll_device_flow",
            lambda fid: {"status": "pending", "interval": 5},
        )
        r = client.get("/api/auth/github/poll?flow_id=f1")
        assert r.status_code == 202
        assert r.json() == {"status": "pending", "interval": 5}

    def test_poll_ok_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        monkeypatch.setattr(
            av.github_auth, "poll_device_flow",
            lambda fid: {"status": "ok", "login": "octocat"},
        )
        r = client.get("/api/auth/github/poll?flow_id=f1")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "login": "octocat"}

    def test_poll_expired_410(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        monkeypatch.setattr(
            av.github_auth, "poll_device_flow", lambda fid: {"status": "expired"},
        )
        r = client.get("/api/auth/github/poll?flow_id=f1")
        assert r.status_code == 410

    def test_poll_denied_403(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        monkeypatch.setattr(
            av.github_auth, "poll_device_flow", lambda fid: {"status": "denied"},
        )
        r = client.get("/api/auth/github/poll?flow_id=f1")
        assert r.status_code == 403

    def test_poll_error_400(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        monkeypatch.setattr(
            av.github_auth, "poll_device_flow",
            lambda fid: {"status": "error", "detail": "unknown_flow"},
        )
        r = client.get("/api/auth/github/poll?flow_id=f1")
        assert r.status_code == 400
        assert r.json() == {"status": "error", "detail": "unknown_flow"}

    # -- GET /api/auth/github/status -----------------------------------------

    def test_status_unauthenticated_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        monkeypatch.setattr(
            av.github_auth, "status_payload", lambda: {"authenticated": False},
        )
        r = client.get("/api/auth/github/status")
        assert r.status_code == 200
        assert r.json() == {"authenticated": False}

    def test_status_authenticated_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        payload = {
            "authenticated": True, "login": "octocat",
            "source": "device_flow", "scopes": ["repo"],
        }
        monkeypatch.setattr(av.github_auth, "status_payload", lambda: payload)
        r = client.get("/api/auth/github/status")
        assert r.status_code == 200
        assert r.json() == payload

    # -- POST /api/auth/github/logout ----------------------------------------

    def test_logout_200_calls_logout(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        called = {"n": 0}
        monkeypatch.setattr(
            av.github_auth, "logout",
            lambda: called.__setitem__("n", called["n"] + 1),
        )
        r = client.post("/api/auth/github/logout", json={})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert called["n"] == 1

    # -- GET /api/auth/github/orgs -------------------------------------------

    def test_orgs_unauthenticated_401(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        monkeypatch.setattr(
            av.github_auth, "list_orgs", lambda: {"error": "unauthenticated"},
        )
        r = client.get("/api/auth/github/orgs")
        assert r.status_code == 401
        assert r.json() == {"error": "unauthenticated"}

    def test_orgs_other_error_502(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        monkeypatch.setattr(
            av.github_auth, "list_orgs",
            lambda: {"error": "orgs_lookup_failed", "status": 500},
        )
        r = client.get("/api/auth/github/orgs")
        assert r.status_code == 502
        assert r.json()["error"] == "orgs_lookup_failed"

    def test_orgs_success_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import auth_views as av
        payload = {
            "login": "octocat",
            "orgs": [{"name": "octocat", "kind": "personal"}],
        }
        monkeypatch.setattr(av.github_auth, "list_orgs", lambda: payload)
        r = client.get("/api/auth/github/orgs")
        assert r.status_code == 200
        assert r.json() == payload

    # -- OpenAPI registration for all 5 --------------------------------------

    def test_routes_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        for p, method in (
            ("/api/auth/github/start", "post"),
            ("/api/auth/github/poll", "get"),
            ("/api/auth/github/status", "get"),
            ("/api/auth/github/logout", "post"),
            ("/api/auth/github/orgs", "get"),
        ):
            assert p in paths and method in paths[p], (p, method)
        assert "AuthPayload" in spec["components"]["schemas"]


# ===========================================================================
# C-state-3f: git-subprocess commit/push routes
#   POST /api/branch/push  +  POST /api/dirty-commit-all
# Every test monkeypatches the lib seam (git_status / work_state / subprocess)
# reached via the git_commit_views module — no test ever runs real git.  The 2
# POSTs pass CSRF because the TestClient sends no Origin header.
# ===========================================================================
class TestGitCommitRoutes:
    # -- POST /api/branch/push -----------------------------------------------

    def test_branch_push_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import git_commit_views as gcv
        seen = {}

        def _fake(ws_root, message):
            seen["message"] = message
            return {"ok": True, "pushed": True, "commit": "abc123", "branch": "feat/x"}

        monkeypatch.setattr(gcv.git_status, "remote_commit_and_push", _fake)
        r = client.post("/api/branch/push", json={"message": "my msg"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "pushed": True, "commit": "abc123", "branch": "feat/x"}
        assert seen["message"] == "my msg"

    def test_branch_push_default_message_when_omitted(self, client, monkeypatch):
        from vivarium_dashboard.lib import git_commit_views as gcv
        seen = {}
        monkeypatch.setattr(
            gcv.git_status, "remote_commit_and_push",
            lambda ws, msg: seen.setdefault("msg", msg)
            or {"ok": True, "pushed": False, "commit": "x", "branch": "b"},
        )
        r = client.post("/api/branch/push")  # no body → BranchPushRequest defaults
        assert r.status_code == 200
        assert seen["msg"] == "dashboard commit"

    def test_branch_push_not_a_git_repo_409(self, client, monkeypatch):
        from vivarium_dashboard.lib import git_commit_views as gcv
        from vivarium_dashboard.lib import git_status as gs

        def _raise(ws, msg):
            raise gs.NotAGitRepo("active source is not a git workspace (no commit/push)")

        monkeypatch.setattr(gcv.git_status, "remote_commit_and_push", _raise)
        r = client.post("/api/branch/push", json={"message": "m"})
        assert r.status_code == 409
        assert r.json() == {"error": "active source is not a git workspace (no commit/push)"}

    def test_branch_push_error_500(self, client, monkeypatch):
        from vivarium_dashboard.lib import git_commit_views as gcv

        def _raise(ws, msg):
            raise RuntimeError("git push failed: boom")

        monkeypatch.setattr(gcv.git_status, "remote_commit_and_push", _raise)
        r = client.post("/api/branch/push", json={})
        assert r.status_code == 500
        assert r.json() == {"error": "git push failed: boom"}

    # -- POST /api/dirty-commit-all ------------------------------------------

    def test_dirty_commit_all_no_workstream_409(self, client, monkeypatch):
        from vivarium_dashboard.lib import git_commit_views as gcv
        monkeypatch.setattr(gcv.work_state, "load_state_or_adopt_current", lambda: {})
        r = client.post("/api/dirty-commit-all", json={})
        assert r.status_code == 409
        assert r.json() == {"error": "no active workstream"}

    def test_dirty_commit_all_happy_200(self, client, monkeypatch):
        import subprocess
        from vivarium_dashboard.lib import git_commit_views as gcv

        monkeypatch.setattr(
            gcv.work_state, "load_state_or_adopt_current", lambda: {"active_branch": "feat/x"}
        )
        calls = []

        def _fake_run(argv, *args, **kwargs):
            calls.append(argv)
            if "rev-parse" in argv and "--abbrev-ref" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout="feat/x", stderr="")
            if argv[:3] == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(argv, 0, stdout="0123456789", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(gcv.subprocess, "run", _fake_run)
        # staged-style porcelain ("M  path") survives the legacy ``.strip()``
        monkeypatch.setattr(gcv.git_status, "dirty_workspace", lambda ws: "M  scripts/a.py")
        monkeypatch.setattr(
            gcv.git_status, "suggest_dirty_commit_message",
            lambda paths: "chore(scripts): commit 1 pending file",
        )
        r = client.post("/api/dirty-commit-all", json={})
        assert r.status_code == 200
        assert r.json() == {
            "commit_sha": "0123456",
            "message": "chore(scripts): commit 1 pending file",
            "paths": ["scripts/a.py"],
        }
        # the pbg-template identity flags + reports/ reset were used
        assert any(a[:5] == ["git", "reset", "HEAD", "--", "reports/"] for a in calls)
        commit_call = next(a for a in calls if "commit" in a)
        assert commit_call == [
            "git", "-c", "user.email=pbg-template@local",
                  "-c", "user.name=pbg-template",
                  "commit", "-m", "chore(scripts): commit 1 pending file",
        ]

    def test_dirty_commit_all_already_clean_409(self, client, monkeypatch):
        import subprocess
        from vivarium_dashboard.lib import git_commit_views as gcv

        monkeypatch.setattr(
            gcv.work_state, "load_state_or_adopt_current", lambda: {"active_branch": "feat/x"}
        )
        monkeypatch.setattr(
            gcv.subprocess, "run",
            lambda argv, *a, **k: subprocess.CompletedProcess(argv, 0, stdout="feat/x", stderr=""),
        )
        monkeypatch.setattr(gcv.git_status, "dirty_workspace", lambda ws: "")
        r = client.post("/api/dirty-commit-all", json={})
        assert r.status_code == 409
        assert r.json() == {"error": "working tree is already clean"}

    # -- OpenAPI registration ------------------------------------------------

    def test_routes_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        assert "/api/branch/push" in paths and "post" in paths["/api/branch/push"]
        assert "/api/dirty-commit-all" in paths and "post" in paths["/api/dirty-commit-all"]
        schemas = spec["components"]["schemas"]
        assert "BranchPushResponse" in schemas
        assert "DirtyCommitAllResponse" in schemas


# ===========================================================================
# C-state-3f2: workstream-lifecycle routes
#   POST /api/work-start /api/work-push /api/work-end /api/work-attach-report
# Tests monkeypatch the pure lib.work_mutations builders reached via the app's
# _work_mutations seam — asserting the route preserves the lib status code via
# JSONResponse (a plain model return would force 200).  No test runs real git.
# The POSTs pass CSRF because the TestClient sends no Origin header.
# ===========================================================================
class TestWorkstreamRoutes:
    # -- POST /api/work-start ------------------------------------------------

    def test_work_start_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import work_mutations as wm
        seen = {}

        def _fake(ws, body):
            seen["body"] = body
            return {"ok": True, "branch": "feat/x", "base": "main"}, 200

        monkeypatch.setattr(wm, "work_start", _fake)
        r = client.post("/api/work-start", json={"branch": "feat/x"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "branch": "feat/x", "base": "main"}
        assert seen["body"] == {"branch": "feat/x"}  # exclude_none drops base

    def test_work_start_invalid_branch_400(self, client, monkeypatch):
        from vivarium_dashboard.lib import work_mutations as wm
        monkeypatch.setattr(wm, "work_start",
                            lambda ws, body: ({"error": "invalid branch name"}, 400))
        r = client.post("/api/work-start", json={"branch": ""})
        assert r.status_code == 400
        assert r.json() == {"error": "invalid branch name"}

    # -- POST /api/work-push -------------------------------------------------

    def test_work_push_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import work_mutations as wm
        monkeypatch.setattr(wm, "work_push",
                            lambda ws, body: ({"ok": True, "branch": "feat/x", "log": "ok"}, 200))
        r = client.post("/api/work-push", json={})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "branch": "feat/x", "log": "ok"}

    def test_work_push_no_origin_409(self, client, monkeypatch):
        from vivarium_dashboard.lib import work_mutations as wm
        body = {
            "error": "no GitHub remote configured",
            "diagnosis": {"category": "no_origin", "summary": "s", "suggestion": "x"},
        }
        monkeypatch.setattr(wm, "work_push", lambda ws, b: (body, 409))
        r = client.post("/api/work-push", json={})
        assert r.status_code == 409
        assert r.json() == body

    # -- POST /api/work-end --------------------------------------------------

    def test_work_end_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import work_mutations as wm
        monkeypatch.setattr(wm, "work_end", lambda ws, body: ({"ok": True}, 200))
        r = client.post("/api/work-end", json={})
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_work_end_no_workstream_409(self, client, monkeypatch):
        from vivarium_dashboard.lib import work_mutations as wm
        monkeypatch.setattr(wm, "work_end",
                            lambda ws, body: ({"error": "no active workstream"}, 409))
        r = client.post("/api/work-end", json={})
        assert r.status_code == 409
        assert r.json() == {"error": "no active workstream"}

    # -- POST /api/work-attach-report ----------------------------------------

    def test_work_attach_report_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import work_mutations as wm
        seen = {}

        def _fake(ws, body):
            seen["body"] = body
            return {"ok": True, "path": "reports/r.html", "branch": "feat/x",
                    "commit_sha": "abc"}, 200

        monkeypatch.setattr(wm, "work_attach_report", _fake)
        r = client.post("/api/work-attach-report",
                        json={"filename": "r.html", "html": "<x>"})
        assert r.status_code == 200
        assert r.json()["commit_sha"] == "abc"
        assert seen["body"] == {"filename": "r.html", "html": "<x>"}

    def test_work_attach_report_no_branch_409(self, client, monkeypatch):
        from vivarium_dashboard.lib import work_mutations as wm
        monkeypatch.setattr(wm, "work_attach_report",
                            lambda ws, body: ({"error": "no active investigation branch"}, 409))
        r = client.post("/api/work-attach-report", json={"filename": "r.html", "html": "<x>"})
        assert r.status_code == 409
        assert r.json() == {"error": "no active investigation branch"}

    # -- OpenAPI registration ------------------------------------------------

    def test_routes_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        for p in ("/api/work-start", "/api/work-push", "/api/work-end",
                  "/api/work-attach-report"):
            assert p in paths and "post" in paths[p], p
        schemas = spec["components"]["schemas"]
        for name in ("WorkStartResponse", "WorkPushResponse", "WorkEndResponse",
                     "WorkAttachReportResponse"):
            assert name in schemas, name


# ===========================================================================
# C-state-3h1: workspace-registry routes
#   POST /api/workspaces/add /api/workspaces/forget /api/workspaces/cleanup-stale
# Tests monkeypatch the pure lib.workspaces_mutations builders reached via the
# app's _workspaces_mut seam — asserting the route preserves the lib status
# code via JSONResponse (a plain model return would force 200) and that an
# omitted-path body reaches the lib's 400 (NOT FastAPI's 422).  No test touches
# the real ~/.pbg catalog.  The POSTs pass CSRF (TestClient sends no Origin).
# ===========================================================================
class TestWorkspacesRegistryRoutes:
    # -- POST /api/workspaces/add --------------------------------------------

    def test_add_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_mutations as wm
        seen = {}

        def _fake(body):
            seen["body"] = body
            return {"name": "demo", "path": "/abs/ws"}, 200

        monkeypatch.setattr(wm, "workspaces_add", _fake)
        r = client.post("/api/workspaces/add", json={"path": "/abs/ws"})
        assert r.status_code == 200
        assert r.json() == {"name": "demo", "path": "/abs/ws"}
        assert seen["body"] == {"path": "/abs/ws"}

    def test_add_value_error_400(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_mutations as wm
        monkeypatch.setattr(wm, "workspaces_add",
                            lambda body: ({"error": "not a workspace"}, 400))
        r = client.post("/api/workspaces/add", json={"path": "/abs/ws"})
        assert r.status_code == 400
        assert r.json() == {"error": "not a workspace"}

    def test_add_omitted_path_400_not_422(self, client, monkeypatch):
        # No monkeypatch — exercise the real builder's own validation so an
        # omitted path yields the legacy 400 (NOT FastAPI's 422).
        from vivarium_dashboard.lib import workspaces_mutations as wm
        monkeypatch.setattr(wm, "workspace_catalog", object())  # never reached
        r = client.post("/api/workspaces/add", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "path must be an absolute string"}

    def test_add_no_body_400_not_422(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_mutations as wm
        monkeypatch.setattr(wm, "workspace_catalog", object())
        r = client.post("/api/workspaces/add")  # no body at all
        assert r.status_code == 400
        assert r.json() == {"error": "path must be an absolute string"}

    # -- POST /api/workspaces/forget -----------------------------------------

    def test_forget_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_mutations as wm
        seen = {}

        def _fake(body):
            seen["body"] = body
            return {"ok": True}, 200

        monkeypatch.setattr(wm, "workspaces_forget", _fake)
        r = client.post("/api/workspaces/forget", json={"path": "/abs/ws"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert seen["body"] == {"path": "/abs/ws"}

    def test_forget_running_409(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_mutations as wm
        monkeypatch.setattr(
            wm, "workspaces_forget",
            lambda body: ({"error": "stop the server before forgetting"}, 409))
        r = client.post("/api/workspaces/forget", json={"path": "/abs/ws"})
        assert r.status_code == 409
        assert r.json() == {"error": "stop the server before forgetting"}

    def test_forget_omitted_path_400_not_422(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_mutations as wm
        monkeypatch.setattr(wm, "workspace_catalog", object())
        r = client.post("/api/workspaces/forget", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "path required"}

    # -- POST /api/workspaces/cleanup-stale ----------------------------------

    def test_cleanup_stale_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_mutations as wm
        seen = {}

        def _fake(body):
            seen["body"] = body
            return {"ok": True}, 200

        monkeypatch.setattr(wm, "workspaces_cleanup_stale", _fake)
        r = client.post("/api/workspaces/cleanup-stale", json={"path": "/abs/ws"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert seen["body"] == {"path": "/abs/ws"}

    def test_cleanup_stale_running_409(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_mutations as wm
        monkeypatch.setattr(
            wm, "workspaces_cleanup_stale",
            lambda body: ({"error": "server is still running"}, 409))
        r = client.post("/api/workspaces/cleanup-stale", json={"path": "/abs/ws"})
        assert r.status_code == 409
        assert r.json() == {"error": "server is still running"}

    def test_cleanup_stale_omitted_path_400_not_422(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_mutations as wm
        monkeypatch.setattr(wm, "workspace_catalog", object())
        r = client.post("/api/workspaces/cleanup-stale", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "path required"}

    # -- OpenAPI registration ------------------------------------------------

    def test_routes_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        for p in ("/api/workspaces/add", "/api/workspaces/forget",
                  "/api/workspaces/cleanup-stale"):
            assert p in paths and "post" in paths[p], p
        schemas = spec["components"]["schemas"]
        for name in ("WorkspacesOkResponse", "WorkspaceEntry"):
            assert name in schemas, name


# ===========================================================================
# Workspace process-management POST routes
#   POST /api/workspaces/start  +  POST /api/workspaces/stop
# Tests monkeypatch the pure lib.workspaces_process_views builders reached via
# the app's _workspaces_proc seam — asserting the route preserves the lib status
# code via JSONResponse (a plain model return would force 200) and that an
# omitted-path body reaches the lib's 400 (NOT FastAPI's 422).  No test spawns
# or kills a real process.  The POSTs pass CSRF (TestClient sends no Origin).
# ===========================================================================
class TestWorkspacesProcessRoutes:
    # -- POST /api/workspaces/start ------------------------------------------

    def test_start_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_process_views as wp
        seen = {}

        def _fake(ws_root, body):
            seen["ws_root"] = ws_root
            seen["body"] = body
            return {"url": "http://127.0.0.1:9001", "pid": 777}, 200

        monkeypatch.setattr(wp, "workspaces_start", _fake)
        r = client.post("/api/workspaces/start", json={"path": "/abs/ws"})
        assert r.status_code == 200
        assert r.json() == {"url": "http://127.0.0.1:9001", "pid": 777}
        assert seen["body"] == {"path": "/abs/ws"}

    def test_start_not_in_catalog_400(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_process_views as wp
        monkeypatch.setattr(
            wp, "workspaces_start",
            lambda ws_root, body: ({"error": "workspace not in catalog — Add it first"}, 400))
        r = client.post("/api/workspaces/start", json={"path": "/abs/ws"})
        assert r.status_code == 400
        assert r.json() == {"error": "workspace not in catalog — Add it first"}

    def test_start_timeout_504(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_process_views as wp
        monkeypatch.setattr(
            wp, "workspaces_start",
            lambda ws_root, body: (
                {"error": "start_timeout", "log_path": "/x", "hint": "tail /x"}, 504))
        r = client.post("/api/workspaces/start", json={"path": "/abs/ws"})
        assert r.status_code == 504
        assert r.json()["error"] == "start_timeout"

    def test_start_omitted_path_400_not_422(self, client, monkeypatch):
        # No monkeypatch of the builder — exercise the real builder's own
        # validation so an omitted path yields the legacy 400 (NOT 422).
        from vivarium_dashboard.lib import workspaces_process_views as wp
        monkeypatch.setattr(wp, "workspace_catalog", object())  # never reached
        r = client.post("/api/workspaces/start", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "path must be an absolute string"}

    def test_start_no_body_400_not_422(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_process_views as wp
        monkeypatch.setattr(wp, "workspace_catalog", object())
        r = client.post("/api/workspaces/start")  # no body at all
        assert r.status_code == 400
        assert r.json() == {"error": "path must be an absolute string"}

    # -- POST /api/workspaces/stop -------------------------------------------

    def test_stop_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_process_views as wp
        seen = {}

        def _fake(ws_root, body):
            seen["body"] = body
            return {"ok": True}, 200

        monkeypatch.setattr(wp, "workspaces_stop", _fake)
        r = client.post("/api/workspaces/stop", json={"path": "/abs/ws"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert seen["body"] == {"path": "/abs/ws"}

    def test_stop_self_stop_400(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_process_views as wp
        monkeypatch.setattr(
            wp, "workspaces_stop",
            lambda ws_root, body: (
                {"error": "refusing to stop self — use the terminal: kill 5"}, 400))
        r = client.post("/api/workspaces/stop", json={"path": "/abs/ws"})
        assert r.status_code == 400
        assert r.json() == {"error": "refusing to stop self — use the terminal: kill 5"}

    def test_stop_not_running_400(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_process_views as wp
        monkeypatch.setattr(
            wp, "workspaces_stop",
            lambda ws_root, body: ({"error": "not running"}, 400))
        r = client.post("/api/workspaces/stop", json={"path": "/abs/ws"})
        assert r.status_code == 400
        assert r.json() == {"error": "not running"}

    def test_stop_omitted_path_400_not_422(self, client, monkeypatch):
        from vivarium_dashboard.lib import workspaces_process_views as wp
        monkeypatch.setattr(wp, "workspace_catalog", object())
        r = client.post("/api/workspaces/stop", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "path must be an absolute string"}

    # -- OpenAPI registration ------------------------------------------------

    def test_routes_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        for p in ("/api/workspaces/start", "/api/workspaces/stop"):
            assert p in paths and "post" in paths[p], p


# ===========================================================================
# C-state-3h2: misc FS/render routes
#   POST /api/click  /api/render  /api/feedback-import
# /api/click returns a RAW empty 204 (no JSON body).  render + feedback-import
# delegate to the pure lib.misc_mutations builders (monkeypatched via the app's
# _misc_mut seam) and preserve the lib status code via JSONResponse.  The POSTs
# pass CSRF (TestClient sends no Origin).
# ===========================================================================
class TestMiscFsRoutes:
    # -- POST /api/click (raw empty 204) -------------------------------------

    def test_click_returns_empty_204_and_writes_file(self, tmp_path):
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: tmp_path
        c = TestClient(app)
        r = c.post("/api/click", json={"event": "view", "study": "x"})
        assert r.status_code == 204
        assert r.content == b""            # RAW empty body — NOT a JSON payload
        ev = tmp_path / ".pbg" / "server" / "state" / "events"
        assert ev.is_file()
        import json as _json
        assert _json.loads(ev.read_text().splitlines()[0]) == {
            "event": "view", "study": "x"}

    def test_click_no_body_204(self, tmp_path):
        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: tmp_path
        c = TestClient(app)
        r = c.post("/api/click")  # no body at all → Body(default={})
        assert r.status_code == 204
        assert r.content == b""

    # -- POST /api/render ----------------------------------------------------

    def test_render_happy_200(self, client, monkeypatch):
        monkeypatch.setattr(api_app._misc_mut, "render_dashboard",
                            lambda ws: ({"ok": True}, 200))
        r = client.post("/api/render", json={})
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_render_failure_500(self, client, monkeypatch):
        monkeypatch.setattr(api_app._misc_mut, "render_dashboard",
                            lambda ws: ({"error": "boom"}, 500))
        r = client.post("/api/render", json={})
        assert r.status_code == 500
        assert r.json() == {"error": "boom"}

    # -- POST /api/feedback-import -------------------------------------------

    def test_feedback_import_happy_200(self, client, monkeypatch):
        seen = {}

        def _fake(ws, body):
            seen["ws"] = ws
            seen["body"] = body
            return {"ok": True, "path": "investigations/dnaa/feedback/t.yaml",
                    "n_entries": 2}, 200

        monkeypatch.setattr(api_app._misc_mut, "feedback_import", _fake)
        r = client.post("/api/feedback-import",
                        json={"annotations": {"s": [1, 2]}})
        assert r.status_code == 200
        assert r.json() == {"ok": True,
                            "path": "investigations/dnaa/feedback/t.yaml",
                            "n_entries": 2}
        assert seen["body"] == {"annotations": {"s": [1, 2]}}

    def test_feedback_import_error_400(self, client, monkeypatch):
        monkeypatch.setattr(api_app._misc_mut, "feedback_import",
                            lambda ws, body: ({"error": "bad payload"}, 400))
        r = client.post("/api/feedback-import", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "bad payload"}

    def test_feedback_import_unavailable_500(self, client, monkeypatch):
        monkeypatch.setattr(
            api_app._misc_mut, "feedback_import",
            lambda ws, body: (
                {"error": "pbg-superpowers not available for feedback import"}, 500))
        r = client.post("/api/feedback-import", json={})
        assert r.status_code == 500
        assert r.json() == {
            "error": "pbg-superpowers not available for feedback import"}

    # -- OpenAPI registration ------------------------------------------------

    def test_routes_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        for p in ("/api/click", "/api/render", "/api/feedback-import"):
            assert p in paths and "post" in paths[p], p
        schemas = spec["components"]["schemas"]
        for name in ("RenderResponse", "FeedbackImportResponse"):
            assert name in schemas, name


# ===========================================================================
# P1: 5 study-run / test-run POST routes (under the "Study runs" tag).
# Each route is a thin wrapper over a lib fn; every test monkeypatches that lib
# fn so NO real sim / pytest / subprocess ever runs.  The routes JSONResponse
# all paths, so the lib-returned (dict, status) is preserved verbatim.
# ===========================================================================
class TestStudyRunRoutes:
    def test_study_run_baseline_passthrough(self, client, monkeypatch):
        from vivarium_dashboard.lib import study_runs
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"ran": "baseline", "run_id": "r1"}, 200

        monkeypatch.setattr(study_runs, "run_study_baseline", _fake)
        r = client.post("/api/study-run-baseline", json={"study": "s1", "steps": 7})
        assert r.status_code == 200
        assert r.json() == {"ran": "baseline", "run_id": "r1"}
        # exclude_none keeps steps; study passes through.
        assert captured["body"] == {"study": "s1", "steps": 7}

    def test_study_run_baseline_omitted_steps_absent(self, client, monkeypatch):
        from vivarium_dashboard.lib import study_runs
        captured = {}
        monkeypatch.setattr(
            study_runs, "run_study_baseline",
            lambda ws, body: (captured.update(body=body) or ({"ok": True}, 200)),
        )
        client.post("/api/study-run-baseline", json={"study": "s1"})
        # An OMITTED optional must be ABSENT (not None) so the lib .get() default holds.
        assert captured["body"] == {"study": "s1"}
        assert "steps" not in captured["body"]

    def test_study_run_baseline_error_status_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import study_runs
        monkeypatch.setattr(
            study_runs, "run_study_baseline",
            lambda ws, body: ({"error": "study not found"}, 404),
        )
        r = client.post("/api/study-run-baseline", json={"study": "ghost"})
        assert r.status_code == 404
        assert r.json() == {"error": "study not found"}

    def test_study_run_variant_passthrough(self, client, monkeypatch):
        from vivarium_dashboard.lib import study_runs
        captured = {}

        def _fake(ws, body):
            captured["body"] = body
            return {"ran": "variant"}, 200

        monkeypatch.setattr(study_runs, "run_study_variant", _fake)
        r = client.post(
            "/api/study-run-variant",
            json={"study": "s1", "variant": "v1", "steps": 3},
        )
        assert r.status_code == 200
        assert r.json() == {"ran": "variant"}
        assert captured["body"] == {"study": "s1", "variant": "v1", "steps": 3}

    def test_study_run_variant_422_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import study_runs
        monkeypatch.setattr(
            study_runs, "run_study_variant",
            lambda ws, body: ({"error": "kind: seeds requires n_seeds >= 1"}, 422),
        )
        r = client.post("/api/study-run-variant", json={"study": "s1", "variant": "v1"})
        assert r.status_code == 422
        assert r.json() == {"error": "kind: seeds requires n_seeds >= 1"}

    def test_study_run_all_baselines_passthrough(self, client, monkeypatch):
        from vivarium_dashboard.lib import study_runs
        captured = {}

        def _fake(ws, body):
            captured["body"] = body
            return {"results": [{"name": "b0"}], "errors": []}, 200

        monkeypatch.setattr(study_runs, "run_study_all_baselines", _fake)
        r = client.post("/api/study-run-all-baselines", json={"study": "s1"})
        assert r.status_code == 200
        assert r.json() == {"results": [{"name": "b0"}], "errors": []}
        assert captured["body"] == {"study": "s1"}

    def test_study_tests_run_passthrough(self, client, monkeypatch):
        from vivarium_dashboard.lib import test_run_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"summary": {"passed": 1}, "tests": [], "note": None}, 200

        monkeypatch.setattr(test_run_views, "study_tests_run", _fake)
        r = client.post("/api/study-tests-run", json={"study": "s1"})
        assert r.status_code == 200
        assert r.json() == {"summary": {"passed": 1}, "tests": [], "note": None}
        assert captured["body"] == {"study": "s1"}

    def test_study_tests_run_409_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import test_run_views
        monkeypatch.setattr(
            test_run_views, "study_tests_run",
            lambda ws, body: ({"error": "tests already running"}, 409),
        )
        r = client.post("/api/study-tests-run", json={"study": "s1"})
        assert r.status_code == 409
        assert r.json() == {"error": "tests already running"}

    def test_run_tests_passthrough(self, client, monkeypatch):
        from vivarium_dashboard.lib import test_run_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"returncode": 0, "stdout": "ok", "stderr": ""}, 200

        monkeypatch.setattr(test_run_views, "run_workspace_tests", _fake)
        r = client.post("/api/run-tests", json={})
        assert r.status_code == 200
        assert r.json() == {"returncode": 0, "stdout": "ok", "stderr": ""}
        assert captured["body"] == {}

    def test_run_tests_timeout_500_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import test_run_views
        monkeypatch.setattr(
            test_run_views, "run_workspace_tests",
            lambda ws, body: ({"error": "pytest timed out after 120s"}, 500),
        )
        r = client.post("/api/run-tests", json={})
        assert r.status_code == 500
        assert r.json() == {"error": "pytest timed out after 120s"}

    def test_all_five_routes_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        for p in (
            "/api/study-run-baseline",
            "/api/study-run-variant",
            "/api/study-run-all-baselines",
            "/api/study-tests-run",
            "/api/run-tests",
        ):
            assert p in paths and "post" in paths[p], p
            assert paths[p]["post"]["tags"] == ["Study runs"], p


# ===========================================================================
# Misc POST routes (cont.): suggest + study-report-single + open-window (under
# the "Misc" tag).  Each route is a thin wrapper over a lib.misc_post_views fn;
# every test monkeypatches that lib fn (via the app's _misc_post_views seam) so
# NO real git runs and NO real window opens.  The routes JSONResponse all paths,
# so the lib-returned (dict, status) is preserved verbatim.  The
# study-report-single route additionally merges the ``?skeptic=`` query param
# into the body before calling the builder.
# ===========================================================================
class TestMiscPostRoutes:
    # -- POST /api/suggest ---------------------------------------------------

    def test_suggest_passthrough(self, client, monkeypatch):
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"ok": True, "id": "repo-name-1", "skill_command": "/x",
                    "instructions": "go"}, 200

        monkeypatch.setattr(api_app._misc_post_views, "suggest", _fake)
        r = client.post("/api/suggest",
                        json={"kind": "repo-name", "context_extras": {"a": 1}})
        assert r.status_code == 200
        assert r.json()["id"] == "repo-name-1"
        assert captured["body"] == {"kind": "repo-name", "context_extras": {"a": 1}}

    def test_suggest_invalid_kind_400_preserved(self, client, monkeypatch):
        monkeypatch.setattr(
            api_app._misc_post_views, "suggest",
            lambda ws, body: ({"error": "invalid kind (must be one of ...)"}, 400))
        r = client.post("/api/suggest", json={"kind": "bogus"})
        assert r.status_code == 400
        assert r.json() == {"error": "invalid kind (must be one of ...)"}

    def test_suggest_omitted_extras_absent(self, client, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            api_app._misc_post_views, "suggest",
            lambda ws, body: (captured.update(body=body) or ({"ok": True}, 200)))
        client.post("/api/suggest", json={"kind": "pr-title"})
        # exclude_none keeps an OMITTED optional ABSENT.
        assert captured["body"] == {"kind": "pr-title"}
        assert "context_extras" not in captured["body"]

    # -- POST /api/study-report-single --------------------------------------

    def test_study_report_single_passthrough(self, client, monkeypatch):
        captured = {}

        def _fake(ws, body):
            captured["body"] = body
            return {"html_path": "reports/s1.html", "size_bytes": 9,
                    "study": "s1"}, 200

        monkeypatch.setattr(api_app._misc_post_views, "study_report_single", _fake)
        r = client.post("/api/study-report-single", json={"study": "s1"})
        assert r.status_code == 200
        assert r.json()["html_path"] == "reports/s1.html"
        assert captured["body"] == {"study": "s1"}
        assert "skeptic" not in captured["body"]

    def test_study_report_single_skeptic_query_true(self, client, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            api_app._misc_post_views, "study_report_single",
            lambda ws, body: (captured.update(body=body) or ({"ok": True}, 200)))
        client.post("/api/study-report-single?skeptic=1", json={"study": "s1"})
        assert captured["body"]["skeptic"] is True

    def test_study_report_single_skeptic_query_false(self, client, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            api_app._misc_post_views, "study_report_single",
            lambda ws, body: (captured.update(body=body) or ({"ok": True}, 200)))
        client.post("/api/study-report-single?skeptic=0", json={"study": "s1"})
        assert captured["body"]["skeptic"] is False

    def test_study_report_single_body_skeptic_wins_over_query(self, client, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            api_app._misc_post_views, "study_report_single",
            lambda ws, body: (captured.update(body=body) or ({"ok": True}, 200)))
        # body already has skeptic → query must NOT override it.
        client.post("/api/study-report-single?skeptic=1",
                    json={"study": "s1", "skeptic": False})
        assert captured["body"]["skeptic"] is False

    def test_study_report_single_error_500_preserved(self, client, monkeypatch):
        monkeypatch.setattr(
            api_app._misc_post_views, "study_report_single",
            lambda ws, body: ({"error": "boom"}, 500))
        r = client.post("/api/study-report-single", json={"study": "s1"})
        assert r.status_code == 500
        assert r.json() == {"error": "boom"}

    # -- POST /api/open-window ----------------------------------------------

    def test_open_window_passthrough(self, client, monkeypatch):
        captured = {}

        def _fake(ws, body):
            captured["body"] = body
            return {"ok": True, "url": "http://h/x"}, 200

        monkeypatch.setattr(api_app._misc_post_views, "open_window", _fake)
        r = client.post("/api/open-window", json={"route": "/x"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "url": "http://h/x"}
        assert captured["body"] == {"route": "/x"}

    def test_open_window_no_server_info_503_preserved(self, client, monkeypatch):
        monkeypatch.setattr(
            api_app._misc_post_views, "open_window",
            lambda ws, body: (
                {"error": "server-info file not found - is the dashboard "
                          "running?"}, 503))
        r = client.post("/api/open-window", json={})
        assert r.status_code == 503
        assert "server-info file not found" in r.json()["error"]

    # -- OpenAPI registration ------------------------------------------------

    def test_routes_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        for p in ("/api/suggest", "/api/study-report-single", "/api/open-window"):
            assert p in paths and "post" in paths[p], p
            assert paths[p]["post"]["tags"] == ["Misc"], p


# ===========================================================================
# P2: composite-test-run POST route (detached run launcher, "Composite runs"
# tag).  Thin wrapper over ``lib.composite_test_run_views.composite_test_run`` —
# every test monkeypatches that lib fn so NO real subprocess is spawned.  The
# route JSONResponses all paths, so the lib-returned (dict, status) is preserved
# verbatim (202 happy / 400 missing-id / 429 at-cap / 500 spawn-failure).
# ===========================================================================
class TestCompositeTestRunRoute:
    def test_happy_202(self, client, monkeypatch):
        from vivarium_dashboard.lib import composite_test_run_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"run_id": "demo__1__abc", "status": "running"}, 202

        monkeypatch.setattr(composite_test_run_views, "composite_test_run", _fake)
        r = client.post(
            "/api/composite-test-run",
            json={"id": "demo.spec", "steps": 9},
        )
        assert r.status_code == 202
        assert r.json() == {"run_id": "demo__1__abc", "status": "running"}
        # exclude_none keeps steps; id passes through, omitted optionals absent.
        assert captured["body"] == {"id": "demo.spec", "steps": 9}

    def test_omitted_optionals_absent(self, client, monkeypatch):
        from vivarium_dashboard.lib import composite_test_run_views
        captured = {}
        monkeypatch.setattr(
            composite_test_run_views, "composite_test_run",
            lambda ws, body: (captured.update(body=body) or ({"run_id": "r", "status": "running"}, 202)),
        )
        client.post("/api/composite-test-run", json={"id": "demo.spec"})
        assert captured["body"] == {"id": "demo.spec"}
        for k in ("steps", "overrides", "label", "emit_paths"):
            assert k not in captured["body"]

    def test_missing_id_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import composite_test_run_views
        monkeypatch.setattr(
            composite_test_run_views, "composite_test_run",
            lambda ws, body: ({"error": "missing id"}, 400),
        )
        r = client.post("/api/composite-test-run", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "missing id"}

    def test_at_cap_429_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import composite_test_run_views
        monkeypatch.setattr(
            composite_test_run_views, "composite_test_run",
            lambda ws, body: (
                {"error": "too many runs in progress — wait for one to finish"}, 429),
        )
        r = client.post("/api/composite-test-run", json={"id": "demo.spec"})
        assert r.status_code == 429
        assert r.json() == {
            "error": "too many runs in progress — wait for one to finish"}

    def test_spawn_failure_500_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import composite_test_run_views
        monkeypatch.setattr(
            composite_test_run_views, "composite_test_run",
            lambda ws, body: ({"error": "spawn failed: boom", "run_id": "r"}, 500),
        )
        r = client.post("/api/composite-test-run", json={"id": "demo.spec"})
        assert r.status_code == 500
        assert r.json() == {"error": "spawn failed: boom", "run_id": "r"}

    def test_route_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        p = "/api/composite-test-run"
        assert p in paths and "post" in paths[p]
        assert paths[p]["post"]["tags"] == ["Composite runs"]


# ===========================================================================
# P3: investigation-run-one POST route (ad-hoc "Duplicate run", "Investigation
# runs" tag).  Thin wrapper over
# ``lib.investigation_run_one_views.investigation_run_one`` — every test
# monkeypatches that lib fn so NO real subprocess is spawned.  The route
# JSONResponses all paths, so the lib-returned (dict, status) is preserved
# verbatim (200 happy / 200 run-failure / 400 missing-inv / 404 not-found).
# ===========================================================================
class TestInvestigationRunOneRoute:
    def test_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import investigation_run_one_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"ok": True, "run_id": "demo__1__abc",
                    "investigation": "inv-x", "sim_name": "ad-hoc",
                    "viz_html": {}}, 200

        monkeypatch.setattr(
            investigation_run_one_views, "investigation_run_one", _fake)
        r = client.post(
            "/api/investigation-run-one",
            json={"investigation": "inv-x", "steps": 7},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["run_id"] == "demo__1__abc"
        # exclude_none keeps investigation + steps; omitted optionals absent.
        assert captured["body"] == {"investigation": "inv-x", "steps": 7}

    def test_run_failure_200_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import investigation_run_one_views
        monkeypatch.setattr(
            investigation_run_one_views, "investigation_run_one",
            lambda ws, body: (
                {"ok": False, "run_id": "r", "error": "boom"}, 200),
        )
        r = client.post("/api/investigation-run-one",
                        json={"investigation": "inv-x"})
        assert r.status_code == 200
        assert r.json() == {"ok": False, "run_id": "r", "error": "boom"}

    def test_missing_investigation_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import investigation_run_one_views
        monkeypatch.setattr(
            investigation_run_one_views, "investigation_run_one",
            lambda ws, body: ({"error": "investigation required"}, 400),
        )
        r = client.post("/api/investigation-run-one", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "investigation required"}

    def test_spec_not_found_404_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import investigation_run_one_views
        monkeypatch.setattr(
            investigation_run_one_views, "investigation_run_one",
            lambda ws, body: ({"error": "spec.yaml not found"}, 404),
        )
        r = client.post("/api/investigation-run-one",
                        json={"investigation": "inv-x"})
        assert r.status_code == 404
        assert r.json() == {"error": "spec.yaml not found"}

    def test_route_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        p = "/api/investigation-run-one"
        assert p in paths and "post" in paths[p]
        assert paths[p]["post"]["tags"] == ["Investigation runs"]


# ===========================================================================
# P4: investigation-run POST route (run ALL sims + render viz, "Investigation
# runs" tag).  Thin wrapper over
# ``lib.investigation_run_views.investigation_run`` — every test monkeypatches
# that lib fn so NO real core build / subprocess runs.  The route JSONResponses
# all paths, so the lib-returned (dict, status) is preserved verbatim (200
# summary / 400 missing-name+spec-error / 404 file-not-found / 500 core-build).
# ===========================================================================
class TestInvestigationRunRoute:
    def test_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import investigation_run_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"ran": 2, "rendered": 1}, 200

        monkeypatch.setattr(
            investigation_run_views, "investigation_run", _fake)
        r = client.post("/api/investigation-run", json={"name": "inv-x"})
        assert r.status_code == 200
        assert r.json() == {"ran": 2, "rendered": 1}
        # exclude_none keeps only the provided key.
        assert captured["body"] == {"name": "inv-x"}

    def test_missing_name_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import investigation_run_views
        monkeypatch.setattr(
            investigation_run_views, "investigation_run",
            lambda ws, body: ({"error": "name is required"}, 400))
        r = client.post("/api/investigation-run", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "name is required"}

    def test_core_build_500_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import investigation_run_views
        monkeypatch.setattr(
            investigation_run_views, "investigation_run",
            lambda ws, body: ({"error": "failed to build core: boom"}, 500))
        r = client.post("/api/investigation-run", json={"name": "inv-x"})
        assert r.status_code == 500
        assert r.json() == {"error": "failed to build core: boom"}

    def test_route_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        p = "/api/investigation-run"
        assert p in paths and "post" in paths[p]
        assert paths[p]["post"]["tags"] == ["Investigation runs"]


# ===========================================================================
# P5: investigation-run-unblocked POST route (enumerate + SUBMIT run job,
# "Investigation runs" tag — the FINAL sim-execution port).  Thin wrapper over
# ``lib.run_unblocked_views.investigation_run_unblocked`` — every test
# monkeypatches that lib fn so NO real enumeration / sim runs.  The route
# JSONResponses all paths, so the lib-returned (dict, status) is preserved
# verbatim (202 happy / 400 missing-inv+no-queued / 404 not-found / 500 yaml).
# ===========================================================================
class TestInvestigationRunUnblockedRoute:
    def test_happy_202(self, client, monkeypatch):
        from vivarium_dashboard.lib import run_unblocked_views
        captured = {}
        items = [{"study": "s", "variant": "baseline", "kind": "baseline",
                  "status": "queued"}]

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"job_id": "JZ", "items": items}, 202

        monkeypatch.setattr(
            run_unblocked_views, "investigation_run_unblocked", _fake)
        r = client.post("/api/investigation-run-unblocked",
                        json={"investigation": "inv-x", "studies": ["s"]})
        assert r.status_code == 202
        assert r.json() == {"job_id": "JZ", "items": items}
        # exclude_none keeps investigation + studies; omitted absent.
        assert captured["body"] == {"investigation": "inv-x", "studies": ["s"]}

    def test_missing_investigation_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import run_unblocked_views
        monkeypatch.setattr(
            run_unblocked_views, "investigation_run_unblocked",
            lambda ws, body: ({"error": "investigation is required"}, 400))
        r = client.post("/api/investigation-run-unblocked", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "investigation is required"}

    def test_not_found_404_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import run_unblocked_views
        monkeypatch.setattr(
            run_unblocked_views, "investigation_run_unblocked",
            lambda ws, body: ({"error": "investigation not found: inv-x"}, 404))
        r = client.post("/api/investigation-run-unblocked",
                        json={"investigation": "inv-x"})
        assert r.status_code == 404
        assert r.json() == {"error": "investigation not found: inv-x"}

    def test_no_queued_breakdown_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import run_unblocked_views
        items = [{"study": "s", "variant": "v", "status": "blocked"}]
        monkeypatch.setattr(
            run_unblocked_views, "investigation_run_unblocked",
            lambda ws, body: ({"error": "no variants to queue (1 blocked). …",
                               "items": items}, 400))
        r = client.post("/api/investigation-run-unblocked",
                        json={"investigation": "inv-x"})
        assert r.status_code == 400
        assert r.json()["items"] == items

    def test_route_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        p = "/api/investigation-run-unblocked"
        assert p in paths and "post" in paths[p]
        assert paths[p]["post"]["tags"] == ["Investigation runs"]


# ===========================================================================
# Viz authoring: visualization-preview POST route ("Viz authoring" tag).
# Thin wrapper over ``lib.viz_preview_views.visualization_preview`` — every test
# monkeypatches that lib fn so NO real viz render runs.  The route JSONResponses
# all paths, so the lib-returned (dict, status) is preserved verbatim (200 demo
# happy / 200 demo-render-failure / 400 missing-address / 404 not-registered).
# ===========================================================================
class TestVisualizationPreviewRoute:
    def test_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import viz_preview_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"ok": True, "html": "<b>x</b>",
                    "source_used": "demo", "notes": ""}, 200

        monkeypatch.setattr(
            viz_preview_views, "visualization_preview", _fake)
        r = client.post(
            "/api/visualization-preview",
            json={"address": "local:FakeViz"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["html"] == "<b>x</b>"
        # exclude_none keeps address; omitted optionals absent.
        assert captured["body"] == {"address": "local:FakeViz"}

    def test_demo_render_failure_200_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import viz_preview_views
        monkeypatch.setattr(
            viz_preview_views, "visualization_preview",
            lambda ws, body: (
                {"ok": False, "html": "<p>demo render failed: ValueError: boom</p>",
                 "source_used": "demo", "notes": ""}, 200),
        )
        r = client.post("/api/visualization-preview",
                        json={"address": "local:FakeViz"})
        assert r.status_code == 200  # render failure still 200
        assert r.json()["ok"] is False

    def test_missing_address_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import viz_preview_views
        monkeypatch.setattr(
            viz_preview_views, "visualization_preview",
            lambda ws, body: ({"error": "address is required"}, 400),
        )
        r = client.post("/api/visualization-preview", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "address is required"}

    def test_class_not_registered_404_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import viz_preview_views
        monkeypatch.setattr(
            viz_preview_views, "visualization_preview",
            lambda ws, body: ({"error": "class not registered: local:Nope"}, 404),
        )
        r = client.post("/api/visualization-preview",
                        json={"address": "local:Nope"})
        assert r.status_code == 404
        assert r.json() == {"error": "class not registered: local:Nope"}

    def test_route_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        p = "/api/visualization-preview"
        assert p in paths and "post" in paths[p]
        assert paths[p]["post"]["tags"] == ["Viz authoring"]


# ===========================================================================
# Viz authoring: visualization-preview-instance POST route ("Viz authoring").
# Thin wrapper over ``lib.viz_preview_instance_views.visualization_preview_
# instance`` — every test monkeypatches that lib fn so NO real lookup/render
# runs.  The route JSONResponses all paths, so the lib-returned (dict, status)
# is preserved verbatim (200 stub / delegated, 400 missing-name, 404
# not-registered).
# ===========================================================================
class TestVisualizationPreviewInstanceRoute:
    def test_happy_200(self, client, monkeypatch):
        from vivarium_dashboard.lib import viz_preview_instance_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"ok": True, "html": "<b>stub</b>",
                    "source_used": "stub", "notes": "n"}, 200

        monkeypatch.setattr(
            viz_preview_instance_views, "visualization_preview_instance", _fake)
        r = client.post(
            "/api/visualization-preview-instance",
            json={"name": "viz1"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["source_used"] == "stub"
        # exclude_none keeps name; omitted optionals absent.
        assert captured["body"] == {"name": "viz1"}

    def test_source_passed_through(self, client, monkeypatch):
        from vivarium_dashboard.lib import viz_preview_instance_views
        captured = {}
        monkeypatch.setattr(
            viz_preview_instance_views, "visualization_preview_instance",
            lambda ws, body: (captured.update(body) or {"ok": True}, 200),
        )
        client.post("/api/visualization-preview-instance",
                    json={"name": "viz1", "source": "investigation:abc"})
        assert captured == {"name": "viz1", "source": "investigation:abc"}

    def test_missing_name_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import viz_preview_instance_views
        monkeypatch.setattr(
            viz_preview_instance_views, "visualization_preview_instance",
            lambda ws, body: ({"error": "name is required"}, 400),
        )
        r = client.post("/api/visualization-preview-instance", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "name is required"}

    def test_not_registered_404_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import viz_preview_instance_views
        monkeypatch.setattr(
            viz_preview_instance_views, "visualization_preview_instance",
            lambda ws, body: ({"error": "visualization 'x' not registered"}, 404),
        )
        r = client.post("/api/visualization-preview-instance",
                        json={"name": "x"})
        assert r.status_code == 404
        assert r.json() == {"error": "visualization 'x' not registered"}

    def test_route_in_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        p = "/api/visualization-preview-instance"
        assert p in paths and "post" in paths[p]
        assert paths[p]["post"]["tags"] == ["Viz authoring"]


# ===========================================================================
# Installs: system-deps-install + import-install POST routes ("Installs" tag).
# Thin wrappers over ``lib.install_views`` — every test monkeypatches that lib
# fn so NO real install subprocess is spawned.  Both JSONResponse all paths, so
# the lib-returned (dict, status) is preserved verbatim.  ``model_dump(
# exclude_none=True)`` keeps omitted optionals absent.
# ===========================================================================
class TestSystemDepsInstallRoute:
    def test_happy_passthrough(self, client, monkeypatch):
        from vivarium_dashboard.lib import install_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"ok": True, "log": [], "recheck": []}, 200

        monkeypatch.setattr(install_views, "system_deps_install", _fake)
        r = client.post(
            "/api/system-deps-install",
            json={"name": "mod", "check_names": ["c1"]},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "log": [], "recheck": []}
        assert captured["body"] == {"name": "mod", "check_names": ["c1"]}

    def test_missing_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import install_views
        monkeypatch.setattr(
            install_views, "system_deps_install",
            lambda ws, body: ({"error": "name + check_names required"}, 400),
        )
        r = client.post("/api/system-deps-install", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "name + check_names required"}

    def test_unknown_module_404_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import install_views
        monkeypatch.setattr(
            install_views, "system_deps_install",
            lambda ws, body: ({"error": "unknown module: mod"}, 404),
        )
        r = client.post(
            "/api/system-deps-install",
            json={"name": "mod", "check_names": ["c1"]},
        )
        assert r.status_code == 404
        assert r.json() == {"error": "unknown module: mod"}


class TestImportInstallRoute:
    def test_happy_passthrough(self, client, monkeypatch):
        from vivarium_dashboard.lib import install_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"ok": True, "log": "installed ok"}, 200

        monkeypatch.setattr(install_views, "import_install", _fake)
        r = client.post("/api/import-install", json={"name": "foo"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "log": "installed ok"}
        # exclude_none keeps name; omitted target absent.
        assert captured["body"] == {"name": "foo"}

    def test_target_passed_through(self, client, monkeypatch):
        from vivarium_dashboard.lib import install_views
        captured = {}
        monkeypatch.setattr(
            install_views, "import_install",
            lambda ws, body: (captured.update(body) or {"ok": True, "log": ""}, 200),
        )
        client.post("/api/import-install", json={"name": "foo", "target": "pkg"})
        assert captured == {"name": "foo", "target": "pkg"}

    def test_missing_name_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import install_views
        monkeypatch.setattr(
            install_views, "import_install",
            lambda ws, body: ({"error": "missing name"}, 400),
        )
        r = client.post("/api/import-install", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "missing name"}

    def test_install_failed_500_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import install_views
        monkeypatch.setattr(
            install_views, "import_install",
            lambda ws, body: ({"error": "install failed", "log": "x"}, 500),
        )
        r = client.post("/api/import-install", json={"name": "foo"})
        assert r.status_code == 500
        assert r.json() == {"error": "install failed", "log": "x"}


class TestCatalogInstallRoute:
    def test_happy_passthrough(self, client, monkeypatch):
        from vivarium_dashboard.lib import catalog_install_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"ok": True, "module": "foo", "install_mode": "pypi", "log": "ok"}, 200

        monkeypatch.setattr(catalog_install_views, "catalog_install", _fake)
        r = client.post("/api/catalog-install", json={"name": "foo"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "module": "foo", "install_mode": "pypi", "log": "ok"}
        # exclude_none keeps name; omitted skip_system_deps_check absent.
        assert captured["body"] == {"name": "foo"}

    def test_skip_flag_passed_through(self, client, monkeypatch):
        from vivarium_dashboard.lib import catalog_install_views
        captured = {}
        monkeypatch.setattr(
            catalog_install_views, "catalog_install",
            lambda ws, body: (captured.update(body) or {"ok": True}, 200),
        )
        client.post("/api/catalog-install", json={"name": "foo", "skip_system_deps_check": True})
        assert captured == {"name": "foo", "skip_system_deps_check": True}

    def test_missing_name_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import catalog_install_views
        monkeypatch.setattr(
            catalog_install_views, "catalog_install",
            lambda ws, body: ({"error": "missing name"}, 400),
        )
        r = client.post("/api/catalog-install", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "missing name"}

    def test_not_in_catalog_404_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import catalog_install_views
        monkeypatch.setattr(
            catalog_install_views, "catalog_install",
            lambda ws, body: ({"error": "module 'foo' not in catalog"}, 404),
        )
        r = client.post("/api/catalog-install", json={"name": "foo"})
        assert r.status_code == 404
        assert r.json() == {"error": "module 'foo' not in catalog"}

    def test_system_deps_409_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import catalog_install_views
        body409 = {
            "error": "unmet system dependencies",
            "name": "foo",
            "platform": "darwin",
            "missing": [{"name": "ipopt", "reason": "not found"}],
            "hint": "POST again with skip_system_deps_check=true to proceed anyway, or call /api/system-deps-install first.",
        }
        monkeypatch.setattr(
            catalog_install_views, "catalog_install",
            lambda ws, body: (body409, 409),
        )
        r = client.post("/api/catalog-install", json={"name": "foo"})
        assert r.status_code == 409
        assert r.json() == body409


class TestCatalogUninstallRoute:
    def test_happy_passthrough(self, client, monkeypatch):
        from vivarium_dashboard.lib import catalog_uninstall_views
        captured = {}

        def _fake(ws, body):
            captured["ws"], captured["body"] = ws, body
            return {"ok": True, "module": "foo", "install_mode": "pypi", "log": "ok"}, 200

        monkeypatch.setattr(catalog_uninstall_views, "catalog_uninstall", _fake)
        r = client.post("/api/catalog-uninstall", json={"name": "foo"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "module": "foo", "install_mode": "pypi", "log": "ok"}
        assert captured["body"] == {"name": "foo"}

    def test_missing_name_400_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import catalog_uninstall_views
        monkeypatch.setattr(
            catalog_uninstall_views, "catalog_uninstall",
            lambda ws, body: ({"error": "missing name"}, 400),
        )
        r = client.post("/api/catalog-uninstall", json={})
        assert r.status_code == 400
        assert r.json() == {"error": "missing name"}

    def test_unmanaged_409_preserved(self, client, monkeypatch):
        from vivarium_dashboard.lib import catalog_uninstall_views
        body409 = {
            "error": "foo is required by bar — uninstall the parent(s) first",
            "transitive_via": ["bar"],
            "module": "foo",
        }
        monkeypatch.setattr(
            catalog_uninstall_views, "catalog_uninstall",
            lambda ws, body: (body409, 409),
        )
        r = client.post("/api/catalog-uninstall", json={"name": "foo"})
        assert r.status_code == 409
        assert r.json() == body409


def test_installs_routes_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/api/system-deps-install", "/api/import-install", "/api/catalog-install",
              "/api/catalog-uninstall"):
        assert p in paths and "post" in paths[p], p
        assert paths[p]["post"]["tags"] == ["Installs"], p

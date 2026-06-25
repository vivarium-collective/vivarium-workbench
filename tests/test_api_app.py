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
# Parity: FastAPI route body == lib function result for runs, observables, series
# ---------------------------------------------------------------------------

class TestExplorerServerShimParity:
    """FastAPI explorer route bodies match the lib function results exactly.

    Both the FastAPI routes and the legacy stdlib handlers call the same lib
    functions with the same arguments.  Parity is verified by calling the lib
    function directly and comparing with the FastAPI route response — this
    catches any accidental wrapping/stripping in the route layer.
    """

    def _make_ws(self, tmp_path: Path) -> Path:
        return _make_explorer_workspace(tmp_path)

    def test_runs_parity(self, tmp_path):
        """GET /api/explorer/runs body == {'runs': list_runs(ws)}."""
        from vivarium_dashboard.lib import explorer_data as _ed

        ws = self._make_ws(tmp_path)
        expected_runs = _ed.list_runs(ws)

        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        c = TestClient(app)
        body = c.get("/api/explorer/runs").json()

        assert body == {"runs": expected_runs}

    def test_observables_parity(self, tmp_path):
        """GET /api/explorer/observables?db=… body == list_observables(db, ws)."""
        from vivarium_dashboard.lib import explorer_data as _ed

        ws = self._make_ws(tmp_path)
        db_path = str(ws / "studies" / "demo" / "runs.db")
        expected = _ed.list_observables(db_path, None, workspace=ws)

        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        c = TestClient(app)
        body = c.get(f"/api/explorer/observables?db={db_path}").json()

        assert body == expected

    def test_series_parity(self, tmp_path):
        """GET /api/explorer/series?db=…&paths=… body == get_series(db, specs, ws)."""
        from vivarium_dashboard.lib import explorer_data as _ed

        ws = self._make_ws(tmp_path)
        db_path = str(ws / "studies" / "demo" / "runs.db")
        path_param = "listeners.mass.cell_mass"
        specs = [("listeners.mass.cell_mass", None)]
        expected = _ed.get_series(db_path, specs, 400, None, workspace=ws)

        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws
        c = TestClient(app)
        body = c.get(
            f"/api/explorer/series?db={db_path}&paths={path_param}"
        ).json()

        assert body == expected


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

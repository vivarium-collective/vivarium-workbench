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


def test_new_routes_in_openapi(client):
    components = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("DashConfig", "InvestigationSummary", "DataSourcesPayload",
                 "DataSource", "BibEntry", "ReferencesBibPayload"):
        assert name in components


def test_workspace_default_is_cwd(monkeypatch):
    """get_workspace honors the env var and defaults to cwd."""
    monkeypatch.delenv("VIVARIUM_DASHBOARD_WORKSPACE", raising=False)
    assert get_workspace() == Path(".").resolve()
    monkeypatch.setenv("VIVARIUM_DASHBOARD_WORKSPACE", "/tmp/ws-xyz")
    assert get_workspace() == Path("/tmp/ws-xyz").resolve()

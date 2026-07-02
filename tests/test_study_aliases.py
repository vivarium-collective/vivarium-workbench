"""Behavioral coverage for the surviving ``/api/study/*`` alias in the FastAPI app.

Historical context: the retired stdlib ``server.py`` exposed a large family of
``/api/study-*`` routes as ALIASES of the canonical ``/api/investigation-*``
handlers, tracked in two module-level dispatch tables (``_POST_ROUTE_MAP`` +
``_POST_STUDY_ALIASES`` and ``_GET_STUDY_ALIASES``).  The old tests here asserted
"old key and new key map to the same handler string" — a pure dict-entry mapping
with no behavioral value once dispatch moved to explicit FastAPI
``@app.get``/``@app.post`` decorators.  Those assertions were deleted with the
dispatch tables they tested.

UPDATE (harden/fastapi-routes): the ``/api/study-*`` aliases the old tests
enumerated — ``study-viz-html``, ``study-composites``, ``study-state-tree``,
``study-delete``, ``study-viz-render``, ``study-viz-add``,
``study-set-observables``, ``study-set-conclusion``, ``study-set-description``,
``study-comparison-add``, ``study-comparison-update``, ``study-group-add``,
``study-group-update``, ``study-variant-rebuild``, ``study-sync-runs``,
``study-bigraph-paths``, ``study-analysis-{outputs,file,zip}`` — plus the six
``DELETE`` routes were re-exposed on the FastAPI app.  Their behavioral coverage
now lives in ``test_fastapi_route_gaps.py``.  This file keeps a behavioral check
for the canonical ``/api/study/{slug}`` route.
"""
import yaml


def _study_ws(tmp_path, slug="demo-study"):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: alias-test\n")
    (ws / ".pbg").mkdir()
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": slug,
        "question": "does the study route resolve?",
        "conditions": {"baseline": {"composite": "x.y.z", "params": {"n_steps": 5}}},
    }))
    return ws, slug


def test_study_detail_alias_route_resolves(tmp_path, dashboard_client):
    """GET /api/study/<slug> (the surviving study alias) serves the study spec."""
    ws, slug = _study_ws(tmp_path)
    client = dashboard_client(ws)
    r = client.get(f"/api/study/{slug}")
    assert r.status_code == 200
    assert r.json().get("name") == slug


def test_study_detail_alias_route_404_for_unknown(tmp_path, dashboard_client):
    """A registered route (not a missing one) returns the loader's 404 body."""
    ws, _ = _study_ws(tmp_path)
    client = dashboard_client(ws)
    r = client.get("/api/study/does-not-exist")
    assert r.status_code == 404
    assert "not found" in r.json().get("error", "")

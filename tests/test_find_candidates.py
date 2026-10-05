import pytest
def _core_with_processes():
    from bigraph_schema.core import allocate_core
    from bigraph_schema.edge import Edge
    core = allocate_core()

    class Fits(Edge):
        def inputs(self): return {'m': 'float'}
        def outputs(self): return {'m': 'float'}

    class Extra(Edge):
        def inputs(self): return {'m': 'float', 'other': 'float'}
        def outputs(self): return {'m': 'float'}

    core.register_link('fits', Fits)
    core.register_link('extra', Extra)
    return core


def test_find_candidates_matches_a_face(monkeypatch):
    from vivarium_workbench.env_worker import _find_candidates
    core = _core_with_processes()
    monkeypatch.setattr('vivarium_workbench.env_worker._get_workspace_core', lambda: core)
    out = _find_candidates({'inputs': {'m': 'float'}, 'outputs': {'m': 'float'}})
    assert out['status'] == 'ok'
    addrs = {c['address'] for c in out['candidates']}
    assert any('fits' in a for a in addrs)


def test_no_match_returns_empty_ok(monkeypatch):
    from vivarium_workbench.env_worker import _find_candidates
    core = _core_with_processes()
    monkeypatch.setattr('vivarium_workbench.env_worker._get_workspace_core', lambda: core)
    out = _find_candidates({'inputs': {'zzz': 'float'}, 'outputs': {}})
    assert out['status'] == 'ok' and out['candidates'] == []


def test_unavailable_when_matcher_missing(monkeypatch):
    import vivarium_workbench.env_worker as ew
    monkeypatch.setattr(ew, 'FIND_CANDIDATES_AVAILABLE', False)
    assert ew._find_candidates({'inputs': {}, 'outputs': {}})['status'] == 'unavailable'


def test_find_candidates_endpoint_shape(tmp_path, monkeypatch):
    try:
        from fastapi.testclient import TestClient
    except RuntimeError as e:  # venv lacks the TestClient HTTP dependency
        pytest.skip(str(e))
    from vivarium_workbench.api.app import create_app, get_workspace
    from vivarium_workbench.lib import env_worker_pool

    seen = {}

    class _Pool:
        def call(self, ws, method, params=None, **kw):
            seen.update(method=method, params=params)
            return {"status": "ok", "candidates": []}

    monkeypatch.setattr(env_worker_pool, "get_pool", lambda: _Pool())
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: tmp_path
    c = TestClient(app)
    r = c.post("/api/find-candidates",
               json={"inputs": {"m": "float"}, "outputs": {"m": "float"}})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and isinstance(body["candidates"], list)
    assert seen["method"] == "find_candidates"
    assert seen["params"] == {"inputs": {"m": "float"}, "outputs": {"m": "float"}}
    # empty / malformed bodies are handled, never a 500
    assert c.post("/api/find-candidates", json={}).status_code == 200
    assert c.post("/api/find-candidates", json={"inputs": "x"}).status_code == 200
    assert c.post("/api/find-candidates", content=b"{bad",
                  headers={"content-type": "application/json"}).status_code < 500

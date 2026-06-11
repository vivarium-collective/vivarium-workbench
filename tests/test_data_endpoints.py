"""Tests for the DataSource-layer JSON endpoints and data-source.js.

Sub-project #1: client-fetch seam.
See docs/superpowers/plans/2026-06-10-client-fetch-seam-subproject-1.md.
"""
import json
import yaml
import pytest

from vivarium_dashboard import server


# ---------------------------------------------------------------------------
# Shared fixture — a minimal workspace with a "demo" study.
# Mirrors the _ws fixture pattern in tests/test_study_detail_page.py.
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Workspace with a minimal studies/demo/study.yaml."""
    ws = tmp_path / "ws"
    demo = ws / "studies" / "demo"
    demo.mkdir(parents=True)
    (demo / "study.yaml").write_text(yaml.safe_dump({
        "name": "demo",
        "schema_version": 3,
        "baseline": [{"name": "default", "composite": "demo.Default"}],
        "variants": [],
        "objective": "A demo study for endpoint tests.",
        "status": "draft",
    }))
    monkeypatch.setattr(server, "WORKSPACE", ws)
    return ws


# ---------------------------------------------------------------------------
# Task 1: GET /api/study/<slug>
# ---------------------------------------------------------------------------

def test_api_study_returns_study_detail_spec(tmp_workspace):
    slug = "demo"
    expected = server._study_detail_spec(slug)
    assert expected is not None
    body, code = server.Handler._build_api_study_response(slug)
    assert code == 200
    assert json.loads(body) == json.loads(json.dumps(expected, default=server._json_default))


def test_api_study_returns_404_for_missing(tmp_workspace):
    body, code = server.Handler._build_api_study_response("does-not-exist")
    assert code == 404
    assert "error" in json.loads(body)


# ---------------------------------------------------------------------------
# Task 2: GET /api/config
# ---------------------------------------------------------------------------

def test_api_config_defaults_to_local_server():
    body, code = server.Handler._build_api_config_response()
    assert code == 200
    assert json.loads(body) == {"mode": "local-server"}


# ---------------------------------------------------------------------------
# Task 3: data-source.js structural check
# ---------------------------------------------------------------------------

def test_data_source_js_is_served_and_defines_loaders():
    text = (server.STATIC_DIR / "data-source.js").read_text()
    for token in [
        "window.DataSource", "loadStudy", "loadInvestigation",
        "/api/study/", "/api/iset/", "__DASH_CONFIG__",
    ]:
        assert token in text, f"data-source.js missing token: {token!r}"


# ---------------------------------------------------------------------------
# Task 6: Lock the DataSource interface
# ---------------------------------------------------------------------------

def test_data_source_interface_is_stable():
    text = (server.STATIC_DIR / "data-source.js").read_text()
    for route in ["/api/study/", "/api/iset/", "/api/workspace", "__DASH_CONFIG__"]:
        assert route in text, f"data-source.js missing route: {route!r}"
    assert server.Handler._build_api_study_response("does-not-exist")[1] == 404

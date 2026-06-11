"""Tests for the DataSource-layer JSON endpoints and data-source.js.

Sub-project #1: client-fetch seam.
See docs/superpowers/plans/2026-06-10-client-fetch-seam-subproject-1.md.
Sub-project #2: narrative export / publish.
See docs/superpowers/plans/2026-06-10-narrative-export-subproject-2.md.
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
# Task 5: iset / investigation page uses DataSource shell
# ---------------------------------------------------------------------------

def test_iset_page_shell_has_config_and_data_source():
    """The main SPA template (index.html.j2) must include __DASH_CONFIG__ and
    data-source.js so the DataSource layer is available on all pages the SPA
    renders.  The investigation/iset detail page lives inside the SPA; this
    ensures the seam is in place for hosted-mode swap-in (sub-projects #2/#3).
    """
    text = (server.TEMPLATES_DIR / "index.html.j2").read_text()
    assert "window.__DASH_CONFIG__" in text
    assert "data-source.js" in text


def test_iset_page_walkthrough_references_data_source():
    """walkthrough.js must reference window.DataSource for the iset-report fetch
    so the seam is wired end-to-end in local mode and SnapshotSource can plug in.
    """
    text = (server.STATIC_DIR / "walkthrough.js").read_text()
    assert "window.DataSource" in text


# ---------------------------------------------------------------------------
# Task 6: Lock the DataSource interface
# ---------------------------------------------------------------------------

def test_data_source_interface_is_stable():
    text = (server.STATIC_DIR / "data-source.js").read_text()
    for route in ["/api/study/", "/api/iset/", "/api/workspace", "__DASH_CONFIG__"]:
        assert route in text, f"data-source.js missing route: {route!r}"
    assert server.Handler._build_api_study_response("does-not-exist")[1] == 404


# ---------------------------------------------------------------------------
# FIX 1 regression: the data-source.js <script src> URL in study-detail.html
# must resolve to an existing file through the server's static handler.
# ---------------------------------------------------------------------------

import re as _re


def _static_handler_resolve(url: str):
    """Replicate the server's static-file resolution logic (server.py ~6169-6195).

    Returns the resolved Path if the URL maps to an existing bundled file,
    otherwise None.  Mirrors:
      rel = url.lstrip("/")
      bundled = STATIC_DIR / rel           → serve if exists
      if rel.startswith("assets/"):
          bundled_alt = STATIC_DIR / rel[7:]  → serve if exists
    """
    path_only = url.split("?", 1)[0]
    rel = path_only.lstrip("/")
    bundled = server.STATIC_DIR / rel
    if bundled.is_file():
        return bundled
    if rel.startswith("assets/"):
        bundled_alt = server.STATIC_DIR / rel[len("assets/"):]
        if bundled_alt.is_file():
            return bundled_alt
    return None


def test_study_detail_data_source_script_url_resolves(tmp_workspace):
    """The <script src> for data-source.js in study-detail.html must map to an
    existing bundled file through the server's static handler — not produce a 404.

    Previously the template used ``/static/data-source.js`` which doubled the
    ``static/`` directory prefix (STATIC_DIR / "static/data-source.js" → absent).
    The correct URL is ``/data-source.js`` (root-relative, matches study-detail.js
    convention at the bottom of the same template).
    """
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("demo")
    html = _render_study_detail_html("demo", spec)

    # Extract all <script src="..."> URLs that mention data-source.js.
    srcs = _re.findall(r'<script\s+src="([^"]*data-source\.js[^"]*)"', html)
    assert srcs, "study-detail.html must contain a <script src=...data-source.js...> tag"

    for src in srcs:
        resolved = _static_handler_resolve(src)
        assert resolved is not None, (
            f"<script src={src!r}> does not resolve to an existing bundled file "
            f"through the static handler.  "
            f"Expected a root-relative URL like /data-source.js "
            f"(not /static/data-source.js which doubles the directory prefix)."
        )


# ---------------------------------------------------------------------------
# FIX 2: _build_api_study_response validates slug → returns 400 for bad slug
# ---------------------------------------------------------------------------

def test_api_study_builder_returns_400_for_invalid_slug(tmp_workspace):
    """After moving the slug-regex guard into the builder, an invalid slug must
    return HTTP 400 from the pure builder (not only from do_GET)."""
    body, code = server.Handler._build_api_study_response("../traversal")
    assert code == 400
    assert "error" in json.loads(body)


# ---------------------------------------------------------------------------
# Sub-project #2 — Task 1: pure builders + GET /api/workspace
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace_with_inv(tmp_path, monkeypatch):
    """Workspace with an investigation + study, for Task 1 tests."""
    ws = tmp_path / "ws"
    inv = ws / "investigations" / "test-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "test-inv",
        "title": "Test Investigation",
        "studies": ["demo"],
        "status": "planning",
    }))
    demo = ws / "studies" / "demo"
    demo.mkdir(parents=True)
    (demo / "study.yaml").write_text(yaml.safe_dump({
        "name": "demo",
        "schema_version": 3,
        "baseline": [{"name": "default", "composite": "demo.Default"}],
        "variants": [],
        "objective": "A demo study.",
        "status": "draft",
    }))
    monkeypatch.setattr(server, "WORKSPACE", ws)
    return ws


def test_iset_detail_data_and_workspace_home_data(tmp_workspace_with_inv):
    """_iset_detail_data returns a dict with 'studies'; _workspace_home_data returns
    a dict; _build_api_workspace_response is JSON-parity with _workspace_home_data."""
    iset = server.Handler._iset_detail_data("test-inv")
    assert isinstance(iset, dict) and "studies" in iset

    home = server._workspace_home_data(server.WORKSPACE)
    assert isinstance(home, dict)

    body, code = server.Handler._build_api_workspace_response()
    assert code == 200
    assert json.loads(body) == json.loads(json.dumps(home, default=server._json_default))


def test_iset_detail_data_returns_none_for_missing(tmp_workspace_with_inv):
    """_iset_detail_data returns None when the investigation.yaml doesn't exist."""
    result = server.Handler._iset_detail_data("does-not-exist")
    assert result is None


# ---------------------------------------------------------------------------
# Sub-project #2 — Task 2: data-source.js snapshot mode
# ---------------------------------------------------------------------------

def test_data_source_has_snapshot_mode():
    """data-source.js must define the snapshot URL helpers and mode check."""
    text = (server.STATIC_DIR / "data-source.js").read_text()
    for token in ['mode === "snapshot"', ".json", "_studyUrl", "_isetUrl", "_workspaceUrl"]:
        assert token in text, f"data-source.js missing token: {token!r}"


# ---------------------------------------------------------------------------
# FIX 2: _study_dir flat spec.yaml edge case
# ---------------------------------------------------------------------------

def test_study_dir_flat_spec_yaml_resolves_to_studies_not_investigations(tmp_path, monkeypatch):
    """_study_dir must return studies/<name>/ when that dir exists but only has
    spec.yaml (no study.yaml) — not fall back to investigations/<name>."""
    ws = tmp_path / "ws"
    # Create studies/legacy-study/ with only spec.yaml (no study.yaml)
    study_dir = ws / "studies" / "legacy-study"
    study_dir.mkdir(parents=True)
    (study_dir / "spec.yaml").write_text("name: legacy-study\n")
    # Ensure investigations/legacy-study/ does NOT exist (fallback target)
    (ws / "investigations").mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text("name: test-ws\n")

    monkeypatch.setattr(server, "WORKSPACE", ws)
    server._WP_CACHE.clear()

    result = server._study_dir("legacy-study")
    assert result == study_dir, (
        f"_study_dir returned {result!r}, expected {study_dir!r} "
        f"(flat studies/<name>/ with spec.yaml only must not fall back to investigations/<name>)"
    )


# ---------------------------------------------------------------------------
# Task 2 (read-only viewer): DataSource loaders for the 5 home-SPA resources
# ---------------------------------------------------------------------------

def test_data_source_has_home_spa_loaders():
    """data-source.js must define the five new loaders + their snapshot URLs."""
    text = (server.STATIC_DIR / "data-source.js").read_text()
    for token in [
        "loadIsetList", "loadInputs", "loadCatalog", "loadComposites", "loadRegistry",
        '"snapshot"',
        "/api/iset-list.json", "/api/inputs/", "/api/catalog.json",
        "/api/composites.json", "/api/registry.json",
    ]:
        assert token in text, f"data-source.js missing token: {token!r}"


def test_walkthrough_routes_reads_through_data_source():
    """walkthrough.js must route each of the 5 home-SPA fetches through DataSource."""
    text = (server.STATIC_DIR / "walkthrough.js").read_text()
    for symbol in [
        "DataSource.loadIsetList",
        "DataSource.loadInputs",
        "DataSource.loadCatalog",
        "DataSource.loadComposites",
        "DataSource.loadRegistry",
    ]:
        assert symbol in text, f"walkthrough.js missing DataSource routing: {symbol!r}"


# ---------------------------------------------------------------------------
# Kept-tab reads: new loaders + empty-slug _inputsUrl fix
# ---------------------------------------------------------------------------

def test_data_source_has_new_kept_tab_loaders():
    """data-source.js must define loadDataSources + loadInvestigationsFlat loaders
    with correct snapshot URLs, and _inputsUrl must route empty slug to _global.json."""
    text = (server.STATIC_DIR / "data-source.js").read_text()

    # New loaders present
    for token in ["loadDataSources", "loadInvestigationsFlat"]:
        assert token in text, f"data-source.js missing loader: {token!r}"

    # Snapshot URLs for the new loaders
    assert "/api/data-sources.json" in text, \
        "data-source.js missing snapshot URL /api/data-sources.json"
    assert "/api/investigations.json" in text, \
        "data-source.js missing snapshot URL /api/investigations.json"

    # Empty-slug _inputsUrl fix: snapshot → _global.json
    assert "_global.json" in text, \
        "data-source.js missing _global.json for empty-slug _inputsUrl"


def test_walkthrough_routes_new_kept_tab_fetches_through_data_source():
    """walkthrough.js must route _loadDataSources and _loadInvestigations
    (+ rail refresh) through DataSource loaders."""
    text = (server.STATIC_DIR / "walkthrough.js").read_text()
    for symbol in [
        "DataSource.loadDataSources",
        "DataSource.loadInvestigationsFlat",
    ]:
        assert symbol in text, \
            f"walkthrough.js missing DataSource routing for kept-tab read: {symbol!r}"


def test_snapshot_readonly_css_hides_explore_button():
    """snapshot-readonly.css must contain a rule hiding the Explore button on
    composite cards, since composite resolution (build_core) requires a live server."""
    text = (server.STATIC_DIR / "snapshot-readonly.css").read_text()
    assert "_openCompositeExplorer" in text, \
        "snapshot-readonly.css missing rule to hide the Explore/composite explorer button"


def test_switchpage_gates_composite_explore_in_snapshot():
    """walkthrough.js _switchPage must redirect composite-explore → simulation-setup
    in snapshot mode so the Explore button (even if somehow clicked) cannot open
    the live-only explorer."""
    text = (server.STATIC_DIR / "walkthrough.js").read_text()
    assert "composite-explore" in text
    # The gate must redirect to simulation-setup, not crash
    assert "simulation-setup" in text

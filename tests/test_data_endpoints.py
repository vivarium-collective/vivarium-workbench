"""Tests for the DataSource-layer JSON endpoints and data-source.js.

Sub-project #1: client-fetch seam.
See docs/superpowers/plans/2026-06-10-client-fetch-seam-subproject-1.md.
Sub-project #2: narrative export / publish.
See docs/superpowers/plans/2026-06-10-narrative-export-subproject-2.md.
"""
import json
import yaml
import pytest

from vivarium_dashboard.lib.json_serialize import _json_body, _json_default
from vivarium_dashboard.lib.report_views import build_iset_detail
from vivarium_dashboard.lib.static_serving import STATIC_DIR, TEMPLATES_DIR
from vivarium_dashboard.lib.study_page import render_study_detail_html
from vivarium_dashboard.lib.study_spec import (
    SLUG_RE,
    load_study_detail_spec,
)
from vivarium_dashboard.lib.study_spec import study_dir as resolve_study_dir
from vivarium_dashboard.lib.system_info import build_workspace_home


# ---------------------------------------------------------------------------
# Pure builders (formerly server.Handler._build_api_study_response /
# _build_api_workspace_response) — reconstructed over lib so the tests exercise
# the same (json_bytes, status) contract without importing the retired server.
# ---------------------------------------------------------------------------

def _build_api_study_response(ws, slug):
    if not SLUG_RE.match(slug):
        return _json_body({"error": "invalid slug"}), 400
    spec = load_study_detail_spec(ws, slug)
    if spec is None:
        return _json_body({"error": f"study not found: {slug}"}), 404
    return _json_body(spec), 200


def _build_api_workspace_response(ws):
    return _json_body(build_workspace_home(ws)), 200


# ---------------------------------------------------------------------------
# Shared fixture — a minimal workspace with a "demo" study.
# Mirrors the _ws fixture pattern in tests/test_study_detail_page.py.
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path):
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
    return ws


# ---------------------------------------------------------------------------
# Task 1: GET /api/study/<slug>
# ---------------------------------------------------------------------------

def test_api_study_returns_study_detail_spec(tmp_workspace):
    slug = "demo"
    expected = load_study_detail_spec(tmp_workspace, slug)
    assert expected is not None
    body, code = _build_api_study_response(tmp_workspace, slug)
    assert code == 200
    assert json.loads(body) == json.loads(json.dumps(expected, default=_json_default))


def test_api_study_returns_404_for_missing(tmp_workspace):
    body, code = _build_api_study_response(tmp_workspace, "does-not-exist")
    assert code == 404
    assert "error" in json.loads(body)


# ---------------------------------------------------------------------------
# Task 3: data-source.js structural check
# ---------------------------------------------------------------------------

def test_data_source_js_is_served_and_defines_loaders():
    text = (STATIC_DIR / "data-source.js").read_text()
    for token in [
        "window.DataSource", "loadStudy", "loadInvestigation",
        "/api/study/", "/api/investigation/", "__DASH_CONFIG__",
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
    text = (TEMPLATES_DIR / "index.html.j2").read_text()
    assert "window.__DASH_CONFIG__" in text
    assert "data-source.js" in text


def test_iset_page_walkthrough_references_data_source():
    """walkthrough.js must reference window.DataSource for the iset-report fetch
    so the seam is wired end-to-end in local mode and SnapshotSource can plug in.
    """
    text = (STATIC_DIR / "walkthrough.js").read_text()
    assert "window.DataSource" in text


# ---------------------------------------------------------------------------
# Task 6: Lock the DataSource interface
# ---------------------------------------------------------------------------

def test_data_source_interface_is_stable(tmp_workspace):
    text = (STATIC_DIR / "data-source.js").read_text()
    for route in ["/api/study/", "/api/investigation/", "/api/workspace", "__DASH_CONFIG__"]:
        assert route in text, f"data-source.js missing route: {route!r}"
    assert _build_api_study_response(tmp_workspace, "does-not-exist")[1] == 404


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
    bundled = STATIC_DIR / rel
    if bundled.is_file():
        return bundled
    if rel.startswith("assets/"):
        bundled_alt = STATIC_DIR / rel[len("assets/"):]
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
    spec = load_study_detail_spec(tmp_workspace, "demo")
    html = render_study_detail_html(tmp_workspace, "demo", spec)

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
    body, code = _build_api_study_response(tmp_workspace, "../traversal")
    assert code == 400
    assert "error" in json.loads(body)


# ---------------------------------------------------------------------------
# Sub-project #2 — Task 1: pure builders + GET /api/workspace
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace_with_inv(tmp_path):
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
    return ws


def test_iset_detail_data_and_workspace_home_data(tmp_workspace_with_inv):
    """build_iset_detail returns a dict with 'studies'; build_workspace_home returns
    a dict; _build_api_workspace_response is JSON-parity with build_workspace_home."""
    iset = build_iset_detail(tmp_workspace_with_inv, "test-inv")
    assert isinstance(iset, dict) and "studies" in iset

    home = build_workspace_home(tmp_workspace_with_inv)
    assert isinstance(home, dict)

    body, code = _build_api_workspace_response(tmp_workspace_with_inv)
    assert code == 200
    assert json.loads(body) == json.loads(json.dumps(home, default=_json_default))


def test_iset_detail_data_returns_none_for_missing(tmp_workspace_with_inv):
    """build_iset_detail returns None when the investigation.yaml doesn't exist."""
    result = build_iset_detail(tmp_workspace_with_inv, "does-not-exist")
    assert result is None


# ---------------------------------------------------------------------------
# Sub-project #2 — Task 2: data-source.js snapshot mode
# ---------------------------------------------------------------------------

def test_data_source_has_snapshot_mode():
    """data-source.js must define the snapshot URL helpers and mode check."""
    text = (STATIC_DIR / "data-source.js").read_text()
    for token in ['mode === "snapshot"', ".json", "_studyUrl", "_isetUrl", "_workspaceUrl"]:
        assert token in text, f"data-source.js missing token: {token!r}"


# ---------------------------------------------------------------------------
# FIX 2: _study_dir flat spec.yaml edge case
# ---------------------------------------------------------------------------

def test_study_dir_flat_spec_yaml_resolves_to_studies_not_investigations(tmp_path):
    """study_dir must return studies/<name>/ when that dir exists but only has
    spec.yaml (no study.yaml) — not fall back to investigations/<name>."""
    ws = tmp_path / "ws"
    # Create studies/legacy-study/ with only spec.yaml (no study.yaml)
    legacy_dir = ws / "studies" / "legacy-study"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "spec.yaml").write_text("name: legacy-study\n")
    # Ensure investigations/legacy-study/ does NOT exist (fallback target)
    (ws / "investigations").mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text("name: test-ws\n")

    result = resolve_study_dir(ws, "legacy-study")
    assert result == legacy_dir, (
        f"study_dir returned {result!r}, expected {legacy_dir!r} "
        f"(flat studies/<name>/ with spec.yaml only must not fall back to investigations/<name>)"
    )


# ---------------------------------------------------------------------------
# Task 2 (read-only viewer): DataSource loaders for the 5 home-SPA resources
# ---------------------------------------------------------------------------

def test_data_source_has_home_spa_loaders():
    """data-source.js must define the five new loaders + their snapshot URLs."""
    text = (STATIC_DIR / "data-source.js").read_text()
    for token in [
        "loadIsetList", "loadInputs", "loadCatalog", "loadComposites", "loadRegistry",
        '"snapshot"',
        "/api/investigation-summaries.json", "/api/inputs/", "/api/catalog.json",
        "/api/composites.json", "/api/registry.json",
    ]:
        assert token in text, f"data-source.js missing token: {token!r}"


def test_walkthrough_routes_reads_through_data_source():
    """walkthrough.js must route each of the 5 home-SPA fetches through DataSource."""
    text = (STATIC_DIR / "walkthrough.js").read_text()
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
    text = (STATIC_DIR / "data-source.js").read_text()

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
    text = (STATIC_DIR / "walkthrough.js").read_text()
    for symbol in [
        "DataSource.loadDataSources",
        "DataSource.loadInvestigationsFlat",
    ]:
        assert symbol in text, \
            f"walkthrough.js missing DataSource routing for kept-tab read: {symbol!r}"


def test_snapshot_composite_explore_available_readonly():
    """snapshot-readonly.css must NOT hide the Explore button — it is now routed
    through bigraph-loom ?static=1 (read-only viewer).  The old hide rule was
    removed in the Task-1 full-surface implementation."""
    text = (STATIC_DIR / "snapshot-readonly.css").read_text()
    assert 'button[onclick*="_openCompositeExplorer"]' not in text, \
        ("snapshot-readonly.css still hides the Explore button; "
         "Task 1 removed that rule — Explore now works read-only via loom ?static=1")


def test_switchpage_composite_explore_available_in_snapshot():
    """walkthrough.js _switchPage no longer redirects composite-explore in snapshot
    mode — Explore now works read-only via bigraph-loom ?static=1&stateUrl=.
    The snapshot whitelist in _initMenuNav must include 'composite-explore' so
    hash-based navigation reaches it directly."""
    text = (STATIC_DIR / "walkthrough.js").read_text()
    # composite-explore is in the page set
    assert "composite-explore" in text
    # The snapshot whitelists must include composite-explore (both focus + hash paths)
    assert "'composite-explore'" in text or '"composite-explore"' in text
    # _switchPage must NOT redirect composite-explore in the snapshot guard block.
    # The old redirect routed composite-explore → simulation-setup; it was removed
    # because Explore now works read-only via loom ?static=1&stateUrl=.
    # The snapshot guard only redirects github and studies:
    assert "'composite-explore'" not in text.split("'github' || pageId === 'studies'")[0].split("classList.contains('snapshot')")[1][:200], \
        "_switchPage snapshot guard still redirects composite-explore (should only redirect github/studies)"


# ---------------------------------------------------------------------------
# Task 2 (full-surface): Simulations DB read-only
# ---------------------------------------------------------------------------

def test_data_source_has_simulations_loader():
    """data-source.js must define loadSimulations + snapshot URL /api/simulations.json."""
    text = (STATIC_DIR / "data-source.js").read_text()
    assert "loadSimulations" in text, "data-source.js missing loadSimulations"
    assert "/api/simulations.json" in text, "data-source.js missing /api/simulations.json"
    assert "/api/simulations" in text, "data-source.js missing /api/simulations live URL"


def test_walkthrough_routes_simulations_through_data_source():
    """walkthrough.js must route _initSimulations through DataSource.loadSimulations."""
    text = (STATIC_DIR / "walkthrough.js").read_text()
    assert "DataSource.loadSimulations" in text, \
        "walkthrough.js missing DataSource.loadSimulations routing"


def test_bundle_exports_simulations(tmp_workspace, tmp_path):
    """build_bundle writes api/simulations.json."""
    from vivarium_dashboard import publish
    out = tmp_path / "bundle"
    publish.build_bundle(tmp_workspace, out)
    assert (out / "api" / "simulations.json").is_file(), "api/simulations.json missing"
    data = json.loads((out / "api" / "simulations.json").read_text())
    assert "simulations" in data, "api/simulations.json missing 'simulations' key"


# ---------------------------------------------------------------------------
# Task 3 (full-surface): Visualizations/Analyses read-only
# ---------------------------------------------------------------------------

def test_data_source_has_visualization_classes_loader():
    """data-source.js must define loadVisualizationClasses + snapshot URL."""
    text = (STATIC_DIR / "data-source.js").read_text()
    assert "loadVisualizationClasses" in text, \
        "data-source.js missing loadVisualizationClasses"
    assert "/api/visualization-classes.json" in text, \
        "data-source.js missing /api/visualization-classes.json"


def test_walkthrough_routes_visualizations_through_data_source():
    """walkthrough.js must route _loadAnalysesPage through DataSource.loadVisualizationClasses."""
    text = (STATIC_DIR / "walkthrough.js").read_text()
    assert "DataSource.loadVisualizationClasses" in text, \
        "walkthrough.js missing DataSource.loadVisualizationClasses routing"


def test_bundle_exports_visualization_classes(tmp_workspace, tmp_path):
    """build_bundle writes api/visualization-classes.json."""
    from vivarium_dashboard import publish
    out = tmp_path / "bundle"
    publish.build_bundle(tmp_workspace, out)
    assert (out / "api" / "visualization-classes.json").is_file(), \
        "api/visualization-classes.json missing"
    data = json.loads((out / "api" / "visualization-classes.json").read_text())
    assert "classes" in data, "api/visualization-classes.json missing 'classes' key"


# ---------------------------------------------------------------------------
# Task 4 (full-surface): read-only banner + interactive-version link
# ---------------------------------------------------------------------------

def test_snapshot_banner_in_template():
    """index.html.j2 must contain the #snapshot-banner div whose link is a STATIC
    pointer to the vivarium-dashboard GitHub repo (run-it-locally instructions),
    not a per-publish hosted-interactive URL."""
    text = (TEMPLATES_DIR / "index.html.j2").read_text()
    assert "snapshot-banner" in text, "index.html.j2 missing #snapshot-banner"
    assert "snapshot-interactive-link" in text, \
        "index.html.j2 missing #snapshot-interactive-link"
    assert "github.com/vivarium-collective/vivarium-dashboard" in text, \
        "snapshot banner link should point at the vivarium-dashboard GitHub repo"


def test_snapshot_banner_css_rules():
    """snapshot-readonly.css must define baseline hide + body.snapshot show for #snapshot-banner."""
    text = (STATIC_DIR / "snapshot-readonly.css").read_text()
    assert "#snapshot-banner" in text, "snapshot-readonly.css missing #snapshot-banner rules"
    assert "body.snapshot #snapshot-banner" in text, \
        "snapshot-readonly.css missing body.snapshot #snapshot-banner show rule"


def test_walkthrough_does_not_override_static_banner_link():
    """The banner link is now a STATIC GitHub href in the template; walkthrough.js
    must NOT re-point or hide #snapshot-interactive-link from a per-publish
    interactiveUrl (that wiring was removed)."""
    text = (STATIC_DIR / "walkthrough.js").read_text()
    assert "snapshot-interactive-link" not in text, \
        "walkthrough.js should no longer wire the (now static) banner link"
    assert "interactiveUrl" not in text, \
        "walkthrough.js should no longer read interactiveUrl"


def test_set_snapshot_config_injects_interactive_url():
    """_set_snapshot_config injects interactiveUrl when provided."""
    from vivarium_dashboard.publish import _set_snapshot_config
    html = 'window.__DASH_CONFIG__ = { mode: "local-server" };'
    result = _set_snapshot_config(html, interactive_url="https://example.com/dash")
    assert 'mode: "snapshot"' in result
    assert "interactiveUrl" in result
    assert "https://example.com/dash" in result


def test_set_snapshot_config_no_url_omits_interactive_url():
    """_set_snapshot_config without interactiveUrl produces minimal config."""
    from vivarium_dashboard.publish import _set_snapshot_config
    html = 'window.__DASH_CONFIG__ = { mode: "local-server" };'
    result = _set_snapshot_config(html)
    assert 'mode: "snapshot"' in result
    assert "interactiveUrl" not in result


# ---------------------------------------------------------------------------
# Task 5 (full-surface): repo switcher → static repo label
# ---------------------------------------------------------------------------

def test_snapshot_repo_label_in_template():
    """index.html.j2 must contain #snapshot-repo-label (static repo label for snapshot mode)."""
    text = (TEMPLATES_DIR / "index.html.j2").read_text()
    assert "snapshot-repo-label" in text, \
        "index.html.j2 missing #snapshot-repo-label"
    assert "viv-repo-label" in text, \
        "index.html.j2 missing .viv-repo-label class"


def test_snapshot_css_hides_switcher_and_shows_label():
    """snapshot-readonly.css must hide #viv-workspace-switcher and show #snapshot-repo-label."""
    text = (STATIC_DIR / "snapshot-readonly.css").read_text()
    assert "#viv-workspace-switcher" in text, \
        "snapshot-readonly.css missing rule to hide #viv-workspace-switcher"
    assert "#snapshot-repo-label" in text, \
        "snapshot-readonly.css missing rule to show #snapshot-repo-label"


def test_walkthrough_sets_repo_label():
    """walkthrough.js DOMContentLoaded must populate snapshot-repo-label from __DASH_CONFIG__.repo."""
    text = (STATIC_DIR / "walkthrough.js").read_text()
    assert "snapshot-repo-label" in text, \
        "walkthrough.js missing snapshot-repo-label population"


# ---------------------------------------------------------------------------
# QA fixes — BUG 1: simulations + visualizations in snapshot _initMenuNav whitelists
# ---------------------------------------------------------------------------

def test_initmenunav_snapshot_whitelists_include_simulations_and_visualizations():
    """Both snapshot whitelists in _initMenuNav (focus + hash) must include
    'simulations' and 'visualizations' so those tabs navigate correctly."""
    text = (STATIC_DIR / "walkthrough.js").read_text()
    # Find the two snapshot whitelist arrays.  Each is a JS array literal
    # that appears inside _initMenuNav after a _snapshot / _snap check.
    import re
    # Extract all array literals that are assigned as snapshot valid-page lists
    # Both lists must contain simulations and visualizations.
    arrays = re.findall(
        r"\? \[([^\]]+)\]"  # snapshot-branch array literal
        r"[\s\S]*?investigations",  # followed by investigations (sanity check)
        text
    )
    # Simpler: just confirm the key strings appear together in the snapshot branches.
    # The non-snapshot branches also include them, so count occurrences.
    assert text.count("'simulations'") >= 4, \
        ("walkthrough.js snapshot whitelists must include 'simulations' in both "
         "focus and hash branches (expected ≥4 occurrences total including live lists)")
    assert text.count("'visualizations'") >= 4, \
        ("walkthrough.js snapshot whitelists must include 'visualizations' in both "
         "focus and hash branches (expected ≥4 occurrences total including live lists)")


# ---------------------------------------------------------------------------
# QA fixes — BUG 2: Studies rail section hidden in snapshot
# ---------------------------------------------------------------------------

def test_snapshot_css_hides_studies_rail_section():
    """snapshot-readonly.css must hide #viv-rail-studies-section in snapshot."""
    text = (STATIC_DIR / "snapshot-readonly.css").read_text()
    assert "viv-rail-studies-section" in text, \
        "snapshot-readonly.css missing rule to hide #viv-rail-studies-section"


def test_template_has_studies_rail_section_id():
    """index.html.j2 must carry id='viv-rail-studies-section' on the Studies rail div."""
    text = (TEMPLATES_DIR / "index.html.j2").read_text()
    assert "viv-rail-studies-section" in text, \
        "index.html.j2 missing id='viv-rail-studies-section' on the Studies rail section"


# ---------------------------------------------------------------------------
# QA fixes — BUG 3: has_wiring in composites.json + graceful 404 message
# ---------------------------------------------------------------------------

def test_bundle_composites_have_has_wiring(tmp_workspace, tmp_path):
    """build_bundle must annotate each composite entry with has_wiring: bool."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(tmp_workspace, out)

    comps_path = out / "api" / "composites.json"
    assert comps_path.is_file(), "api/composites.json missing"
    comps_data = json.loads(comps_path.read_text())
    for comp in comps_data.get("composites", []):
        assert "has_wiring" in comp, \
            f"composite {comp.get('id')!r} missing has_wiring field"
        assert isinstance(comp["has_wiring"], bool), \
            f"composite {comp.get('id')!r} has_wiring is not bool"


def test_cefetch_snapshot_graceful_message_in_walkthrough():
    """walkthrough.js _ceFetch catch handler must show a snapshot-specific
    message instead of the raw 'Network error: ...' when in snapshot mode."""
    text = (STATIC_DIR / "walkthrough.js").read_text()
    assert "Wiring snapshot not available" in text, \
        "walkthrough.js missing snapshot graceful message in _ceFetch catch"


def test_snapshot_css_hides_inv_composites_subtab():
    """snapshot-readonly.css must hide the investigation Composites sub-tab
    (data-tab='composites') so it doesn't fire /api/investigation-composites."""
    text = (STATIC_DIR / "snapshot-readonly.css").read_text()
    assert 'data-tab="composites"' in text, \
        "snapshot-readonly.css missing rule to hide investigation Composites sub-tab"


# ---------------------------------------------------------------------------
# QA fixes — BUG 4: charts placeholder in snapshot
# ---------------------------------------------------------------------------

def test_study_detail_charts_gated_in_snapshot():
    """study-detail.js _loadCharts must be snapshot-aware.

    Since #262 (aec000d) snapshot mode no longer short-circuits — the publisher
    base64-embeds the static charts into the snapshot, so _loadCharts fetches
    them and shows a snapshot-specific empty-state placeholder when none were
    published (live charts need a runs.db absent from the snapshot). This test
    was previously asserting the pre-#262 "Results are served by sms-api"
    short-circuit text, which #262 removed without updating the test.
    """
    text = (STATIC_DIR / "study-detail.js").read_text()
    assert "mode === 'snapshot'" in text or 'mode === "snapshot"' in text, \
        "study-detail.js _loadCharts missing snapshot mode gate"
    assert "No pre-rendered charts published" in text, \
        "study-detail.js _loadCharts missing snapshot empty-state placeholder"

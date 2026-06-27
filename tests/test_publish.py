"""Tests for vivarium_dashboard.publish — narrative export / "publish".

Sub-project #2: narrative export.
See docs/superpowers/plans/2026-06-10-narrative-export-subproject-2.md.
"""
import json
import re
from pathlib import Path

import yaml
import pytest

from vivarium_dashboard import server


# ---------------------------------------------------------------------------
# Shared fixture — a minimal workspace with an investigation + two studies.
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Workspace with one investigation and two studies for publish tests."""
    ws = tmp_path / "ws"
    (ws / "workspace.yaml").parent.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text(yaml.safe_dump({
        "name": "test-ws",
        "description": "A test workspace.",
    }))
    inv = ws / "investigations" / "main-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "main-inv",
        "title": "Main Investigation",
        "studies": ["alpha", "beta"],
        "status": "in_progress",
    }))
    for slug in ("alpha", "beta"):
        sd = ws / "studies" / slug
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "name": slug,
            "schema_version": 3,
            "baseline": [{"name": "core", "composite": f"pkg.{slug}.Core"}],
            "variants": [],
            "objective": f"Objective for {slug}.",
            "status": "draft",
        }))
    monkeypatch.setattr(server, "WORKSPACE", ws)
    return ws


# ---------------------------------------------------------------------------
# Task 3: build_bundle produces the correct layout + JSON parity
# ---------------------------------------------------------------------------

def test_build_bundle_structure_and_parity(tmp_workspace, tmp_path):
    """build_bundle writes the bundle layout and study JSON matches the API builder."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    summary = publish.build_bundle(server.WORKSPACE, out)

    # Bundle root files
    assert (out / "index.html").is_file(), "home shell missing"
    assert (out / "config.json").is_file(), "config.json missing"
    assert (out / "assets" / "data-source.js").is_file(), "data-source.js missing"
    assert (out / "api" / "workspace.json").is_file(), "workspace.json missing"

    # At least one study exported
    assert len(summary["studies"]) >= 1
    slug = summary["studies"][0]

    assert (out / "api" / "study" / f"{slug}.json").is_file(), f"study/{slug}.json missing"
    assert (out / "studies" / slug / "index.html").is_file(), f"study shell missing"

    # JSON parity: bundle file == API builder output
    bundle_json = json.loads((out / "api" / "study" / f"{slug}.json").read_text())
    api_json = json.loads(json.dumps(server._study_detail_spec(slug), default=server._json_default))
    assert bundle_json == api_json, "study JSON parity failed"

    # config.json shape
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["mode"] == "snapshot"
    assert "commit" in cfg


def test_build_bundle_investigation_json(tmp_workspace, tmp_path):
    """build_bundle writes api/investigation/<name>.json with the same data as _iset_detail_data."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    iset_file = out / "api" / "investigation" / "main-inv.json"
    assert iset_file.is_file(), "iset JSON missing"
    iset_data = json.loads(iset_file.read_text())
    assert "studies" in iset_data

    expected = json.loads(json.dumps(server.Handler._iset_detail_data("main-inv"), default=server._json_default))
    assert iset_data == expected, "iset JSON parity failed"


def test_build_bundle_workspace_json(tmp_workspace, tmp_path):
    """build_bundle writes api/workspace.json matching _workspace_home_data."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    ws_file = out / "api" / "workspace.json"
    assert ws_file.is_file()
    ws_data = json.loads(ws_file.read_text())
    expected = json.loads(json.dumps(server._workspace_home_data(server.WORKSPACE), default=server._json_default))
    assert ws_data == expected, "workspace JSON parity failed"


def test_build_bundle_summary_keys(tmp_workspace, tmp_path):
    """summary dict has investigations, studies, out keys."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    summary = publish.build_bundle(server.WORKSPACE, out)
    assert "investigations" in summary
    assert "studies" in summary
    assert "out" in summary
    assert "alpha" in summary["studies"]
    assert "beta" in summary["studies"]
    assert "main-inv" in summary["investigations"]


# ---------------------------------------------------------------------------
# Task 4: every asset URL in every shell resolves to a file in the bundle
# ---------------------------------------------------------------------------

def test_bundle_shell_asset_urls_resolve(tmp_workspace, tmp_path):
    """Every src/href asset URL in every shell (home + per-study) must resolve
    to an existing file in the bundle — no broken relative or root paths."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    shells = [out / "index.html"] + list(out.glob("studies/*/index.html"))
    assert shells, "no shells found"

    for shell in shells:
        html = shell.read_text()
        for m in re.finditer(r'(?:src|href)="(/[^"]+\.(?:js|css))"', html):
            url = m.group(1)
            rel = url.lstrip("/")
            candidate = out / rel
            assert candidate.is_file(), (
                f"{shell.relative_to(out)}: {url!r} does not resolve in bundle "
                f"(expected {candidate})"
            )
        # Snapshot config present in every shell
        assert 'mode: "snapshot"' in html or '"mode":"snapshot"' in html or \
               '"mode": "snapshot"' in html, f"{shell.name}: missing snapshot config"


# ---------------------------------------------------------------------------
# Read-only viewer (full surface): Task 1 — composite-state + loom dist
# ---------------------------------------------------------------------------

def test_bundle_exports_composite_state_and_loom(tmp_workspace, tmp_path):
    """build_bundle writes api/composite-state/<id>.json for each composite and
    copies bigraph-loom dist to bundle/bigraph-loom/ (when bigraph_loom installed)."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    comps = json.loads((out / "api" / "composites.json").read_text())["composites"]
    if comps:
        cid = comps[0]["id"]
        assert (out / "api" / "composite-state" / f"{cid}.json").is_file(), \
            f"api/composite-state/{cid}.json missing"

    # bigraph-loom dist is optional — skipped when bigraph_loom is not installed
    try:
        import bigraph_loom  # noqa: F401
        assert (out / "bigraph-loom" / "index.html").is_file(), \
            "bundle/bigraph-loom/index.html missing"
    except ImportError:
        pass  # package not installed in this venv; loom dist skip is expected


def test_bundle_survives_nonfinite_composite_state(tmp_workspace, tmp_path, monkeypatch):
    """A composite whose resolved state carries a non-finite float (inf/nan)
    must NOT crash the whole bundle build — strict JSON rejects inf/nan, so the
    composite degrades to has_wiring=False (Explore hidden), exactly like an
    unresolvable composite, while finite composites still export their state."""
    from vivarium_dashboard import publish

    monkeypatch.setattr(server, "_composites_data", lambda ws: {"composites": [
        {"id": "good", "name": "Good"},
        {"id": "bad", "name": "Bad"},
    ]})

    def _resolve(cid):
        if cid == "bad":
            return {"state": {"rate": float("inf")}}  # non-finite -> strict JSON rejects
        return {"state": {"rate": 1.0}}

    monkeypatch.setattr(server, "_composite_resolve_data", _resolve)

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)  # must not raise

    by_id = {c["id"]: c for c in
             json.loads((out / "api" / "composites.json").read_text())["composites"]}
    assert by_id["good"]["has_wiring"] is True
    assert by_id["bad"]["has_wiring"] is False
    assert (out / "api" / "composite-state" / "good.json").is_file()
    assert not (out / "api" / "composite-state" / "bad.json").exists()


def test_bundle_uses_committed_composite_state_override(tmp_workspace, tmp_path, monkeypatch):
    """A composite whose generator can't resolve at publish time (e.g. the full
    baseline, which needs the on-disk ParCa cache) still becomes navigable when a
    pre-resolved state JSON is committed under reports/composite-state/<id>.json:
    the committed file is used verbatim and has_wiring=True."""
    from vivarium_dashboard import publish

    monkeypatch.setattr(server, "_composites_data", lambda ws: {"composites": [
        {"id": "heavy.composite", "name": "Heavy"},
    ]})

    def _resolve(cid):
        raise RuntimeError("needs on-disk cache")  # live resolution fails

    monkeypatch.setattr(server, "_composite_resolve_data", _resolve)

    committed_dir = server.WORKSPACE / "reports" / "composite-state"
    committed_dir.mkdir(parents=True, exist_ok=True)
    committed = {"state": {"step": {"_type": "step", "inputs": {}, "outputs": {}}}}
    (committed_dir / "heavy.composite.json").write_text(json.dumps(committed))

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    exported = out / "api" / "composite-state" / "heavy.composite.json"
    assert exported.is_file(), "committed override not exported"
    assert json.loads(exported.read_text()) == committed, "committed override not used verbatim"
    by_id = {c["id"]: c for c in
             json.loads((out / "api" / "composites.json").read_text())["composites"]}
    assert by_id["heavy.composite"]["has_wiring"] is True, "override should mark composite navigable"


# ---------------------------------------------------------------------------
# Task 1 (read-only viewer): export the five read resources
# ---------------------------------------------------------------------------

def test_bundle_exports_full_read_surface(tmp_workspace, tmp_path):
    """build_bundle writes api/{investigation-summaries,catalog,composites,registry}.json
    and api/inputs/<inv>.json per investigation; investigation-summaries parity."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    assert (out / "api" / "investigation-summaries.json").is_file(), "investigation-summaries.json missing"
    assert (out / "api" / "catalog.json").is_file(), "catalog.json missing"
    assert (out / "api" / "composites.json").is_file(), "composites.json missing"
    assert (out / "api" / "registry.json").is_file(), "registry.json missing"

    # inputs per investigation
    isets = json.loads((out / "api" / "investigation-summaries.json").read_text())["investigations"]
    if isets:
        inv = isets[0]["name"]
        assert (out / "api" / "inputs" / f"{inv}.json").is_file(), \
            f"inputs/{inv}.json missing"

    # parity for investigation-summaries
    assert json.loads((out / "api" / "investigation-summaries.json").read_text()) == \
        json.loads(json.dumps(
            {"investigations": server._build_iset_summary_for_test(server.WORKSPACE)},
            default=server._json_default,
        )), "investigation-summaries.json parity failed"


def test_bundle_exports_kept_tab_reads(tmp_workspace, tmp_path):
    """build_bundle writes the three new files needed by kept tabs in the viewer:
    api/inputs/_global.json, api/data-sources.json, api/investigations.json."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    # Global inputs — no investigation slug (empty-slug Sources page)
    global_json = out / "api" / "inputs" / "_global.json"
    assert global_json.is_file(), "api/inputs/_global.json missing"
    global_data = json.loads(global_json.read_text())
    assert "global" in global_data, "api/inputs/_global.json missing 'global' key"

    # Repo-wide data sources (workspace.yaml provider hook)
    ds_json = out / "api" / "data-sources.json"
    assert ds_json.is_file(), "api/data-sources.json missing"
    ds_data = json.loads(ds_json.read_text())
    assert "sources" in ds_data, "api/data-sources.json missing 'sources' key"

    # Flat investigations list with DAG (studies left-rail)
    inv_json = out / "api" / "investigations.json"
    assert inv_json.is_file(), "api/investigations.json missing"
    inv_data = json.loads(inv_json.read_text())
    assert "investigations" in inv_data, "api/investigations.json missing 'investigations' key"
    # The workspace has studies alpha + beta; both must appear
    names = {r["name"] for r in inv_data["investigations"]}
    assert "alpha" in names, "api/investigations.json missing study 'alpha'"
    assert "beta" in names, "api/investigations.json missing study 'beta'"


# ---------------------------------------------------------------------------
# Task 3 (read-only viewer): snapshot read-only mode
# ---------------------------------------------------------------------------

def test_snapshot_readonly_css_exists_and_has_key_rules():
    """snapshot-readonly.css must exist as a static asset and contain the
    key hiding rules for the github rail link and js-authoring.
    Simulations DB + Visualizations tabs are now read-only enabled (full-surface plan)
    so their hide rules were removed."""
    css_path = server.STATIC_DIR / "snapshot-readonly.css"
    assert css_path.is_file(), "snapshot-readonly.css not found in static assets"
    text = css_path.read_text()
    for selector in [
        'body.snapshot',
        'data-page="github"',
        '.js-authoring',
        '#ce-begin-study-bar',
        '#investigation-run-unblocked',
    ]:
        assert selector in text, f"snapshot-readonly.css missing selector: {selector!r}"


def test_snapshot_css_bundled_in_home_shell(tmp_workspace, tmp_path):
    """build_bundle copies snapshot-readonly.css to assets/ and the
    home shell's href resolves to an existing file in the bundle."""
    from vivarium_dashboard import publish
    import re

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    assert (out / "assets" / "snapshot-readonly.css").is_file(), \
        "snapshot-readonly.css not in bundle assets/"

    html = (out / "index.html").read_text()
    assert "snapshot-readonly.css" in html, \
        "index.html does not reference snapshot-readonly.css"

    # Every CSS href must resolve in the bundle
    for m in re.finditer(r'href="(/[^"]+\.css)"', html):
        url = m.group(1)
        rel = url.lstrip("/")
        assert (out / rel).is_file(), \
            f"index.html: {url!r} does not resolve in bundle"


def test_walkthrough_composite_popout_is_snapshot_aware():
    """The report's "What we ran" composite pop-out (_loomStaticPopout) must, in
    snapshot mode, target the STATIC composite-state file under the configured
    base path — /api/composite-state/<id>.json prefixed by basePath — not the
    live query form (/api/composite-state?ref=<id>) at the bare origin, which
    404s on a GitHub Pages project subpath. It must also suppress the pop-out
    link for a composite known to be non-navigable (has_wiring === false).
    Regression: the pop-out opened <origin>/bigraph-loom/... with no base path
    and the live ?ref= query, so every composite link 404'd in the read-only
    dashboard.
    """
    text = (server.STATIC_DIR / "walkthrough.js").read_text()
    # Snapshot branch builds the static .json state path...
    assert "'/api/composite-state/' + encodeURIComponent(composite) + '.json'" in text, \
        "loom pop-out missing snapshot static composite-state path"
    # ...and prefixes both state + loom URLs with the configured base path.
    assert "cfg.basePath" in text and "cfg.mode === 'snapshot'" in text, \
        "loom pop-out not snapshot/basePath aware"
    # Non-navigable composites render plain text instead of a broken pop-out.
    assert "known.has_wiring === false" in text, \
        "composite cell does not suppress pop-out for non-navigable composites"


def test_walkthrough_has_snapshot_body_class_and_switchpage_gating():
    """walkthrough.js must set body.snapshot at DOMContentLoaded and gate
    the github/studies tabs in _switchPage.
    Simulations and Visualizations tabs are now read-only enabled (full-surface plan)
    so their _switchPage redirects were removed."""
    text = (server.STATIC_DIR / "walkthrough.js").read_text()
    assert 'document.body.classList.add("snapshot")' in text, \
        "walkthrough.js missing body.snapshot class init"
    assert '_switchPage' in text
    # _switchPage guard: still redirects github → investigations
    assert '"github"' in text or "'github'" in text
    # The snapshot gating must redirect, not break
    assert 'investigations' in text


# ---------------------------------------------------------------------------
# --base-path: subpath hosting for GitHub Pages project sites
# ---------------------------------------------------------------------------

def test_build_bundle_with_base_path(tmp_workspace, tmp_path):
    """build_bundle(base_path='/v2ecoli/dashboard') prefixes /assets/ and
    /bigraph-loom/ URLs in all shells and injects basePath into __DASH_CONFIG__."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    base = "/v2ecoli/dashboard"
    publish.build_bundle(server.WORKSPACE, out, base_path=base)

    for shell in [out / "index.html"] + list(out.glob("studies/*/index.html")):
        html = shell.read_text()

        # All JS/CSS asset refs must be prefixed
        assert f'{base}/assets/' in html, \
            f"{shell.name}: missing prefixed /assets/ URL"
        # Bare /assets/ must NOT appear (every one should be prefixed)
        for m in re.finditer(r'(?:src|href)="(/assets/[^"]+)"', html):
            raise AssertionError(
                f"{shell.name}: unprefixed /assets/ URL: {m.group(1)!r}"
            )

        # __DASH_CONFIG__ must carry basePath
        assert f'basePath: "{base}"' in html, \
            f"{shell.name}: __DASH_CONFIG__ missing basePath"
        assert 'mode: "snapshot"' in html, \
            f"{shell.name}: __DASH_CONFIG__ missing mode: snapshot"

    # Home shell specifically: loom iframe src must be prefixed
    home_html = (out / "index.html").read_text()
    assert f'{base}/bigraph-loom/' in home_html, \
        "index.html: bigraph-loom iframe src not prefixed with base path"


def test_build_bundle_base_path_normalization(tmp_workspace, tmp_path):
    """base_path is normalized: trailing slash stripped, leading slash added."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    # Trailing slash should be stripped; no leading slash should be added
    publish.build_bundle(server.WORKSPACE, out, base_path="v2ecoli/dashboard/")

    home_html = (out / "index.html").read_text()
    # Canonical form: leading slash, no trailing slash
    assert 'basePath: "/v2ecoli/dashboard"' in home_html, \
        "base_path normalization failed"
    assert "/v2ecoli/dashboard/assets/" in home_html, \
        "assets not prefixed after normalization"


def test_build_bundle_default_base_path_unchanged(tmp_workspace, tmp_path):
    """Default base_path='' leaves root-absolute URLs untouched."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    home_html = (out / "index.html").read_text()

    # basePath must NOT be injected into __DASH_CONFIG__
    assert 'basePath' not in home_html, \
        "basePath injected into __DASH_CONFIG__ by default (should only appear when set)"

    # Asset URLs must be root-absolute (not double-prefixed or missing)
    assert 'src="/assets/' in home_html or 'href="/assets/' in home_html, \
        "index.html: root-absolute /assets/ URLs missing with empty base_path"


def test_data_source_js_has_base_helper():
    """data-source.js must expose a _base() helper that reads cfg().basePath."""
    text = (server.STATIC_DIR / "data-source.js").read_text()
    # _base helper exists
    assert '_base()' in text, "data-source.js missing _base() helper invocation"
    assert 'basePath' in text, "data-source.js missing basePath reference"
    # Snapshot URLs must be prefixed with _base()
    assert '_base() + "/api/' in text or '_base() +' in text, \
        "data-source.js snapshot URLs not prefixed with _base()"


def test_walkthrough_js_loom_stateurl_prefixed_with_base_path():
    """walkthrough.js must prefix the loom iframe src and stateUrl with
    __DASH_CONFIG__.basePath in snapshot mode."""
    text = (server.STATIC_DIR / "walkthrough.js").read_text()
    # basePath is read from __DASH_CONFIG__
    assert '__DASH_CONFIG__' in text and 'basePath' in text, \
        "walkthrough.js missing __DASH_CONFIG__.basePath reference"
    # stateUrl must be prefixed
    assert '_snapshotBase' in text or 'basePath' in text, \
        "walkthrough.js missing base-path prefix for loom stateUrl"


# ---------------------------------------------------------------------------
# Task 5: golden on a real workspace (v2e-invest), skipif absent
# ---------------------------------------------------------------------------

_V2E_INVEST = Path("/Users/eranagmon/code/v2e-invest")


@pytest.mark.skipif(not _V2E_INVEST.is_dir(), reason="v2e-invest not present")
def test_golden_v2e_invest(tmp_path):
    """Export the real v2e-invest workspace; assert real content + JSON parity +
    asset resolution + commit sha.  v2e-invest must stay UNTOUCHED."""
    from vivarium_dashboard import publish

    # Confirm read-only: record the workspace git status BEFORE
    import subprocess
    pre = subprocess.run(
        ["git", "-C", str(_V2E_INVEST), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    pre_dirty = pre.stdout.strip()

    out = tmp_path / "bundle"
    summary = publish.build_bundle(_V2E_INVEST, out)

    # Confirm v2e-invest is still clean
    post = subprocess.run(
        ["git", "-C", str(_V2E_INVEST), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    assert post.stdout.strip() == pre_dirty, "v2e-invest was modified!"

    # At least one investigation + one study exported
    assert len(summary["investigations"]) >= 1, "no investigations exported"
    assert len(summary["studies"]) >= 1, "no studies exported"

    # JSON parity for the first study
    slug = summary["studies"][0]
    bundle_json = json.loads((out / "api" / "study" / f"{slug}.json").read_text())

    # Temporarily set WORKSPACE to v2e-invest to call _study_detail_spec
    orig_ws = server.WORKSPACE
    server.WORKSPACE = _V2E_INVEST
    server._WP_CACHE.clear()
    try:
        api_json = json.loads(json.dumps(server._study_detail_spec(slug), default=server._json_default))
    finally:
        server.WORKSPACE = orig_ws
        server._WP_CACHE.clear()

    assert bundle_json == api_json, "JSON parity failed for real study"

    # All shell asset URLs resolve
    shells = [out / "index.html"] + list(out.glob("studies/*/index.html"))
    for shell in shells:
        html = shell.read_text()
        for m in re.finditer(r'(?:src|href)="(/[^"]+\.(?:js|css))"', html):
            url = m.group(1)
            rel = url.lstrip("/")
            assert (out / rel).is_file(), f"{shell.name}: {url!r} missing in bundle"

    # config.json has a real commit sha
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["mode"] == "snapshot"
    commit = cfg.get("commit")
    assert commit and len(commit) >= 7, f"config.commit is not a sha: {commit!r}"

    # Task 4 (read-only viewer): new API resources present + snapshot-readonly.css
    assert (out / "api" / "investigation-summaries.json").is_file(), "investigation-summaries.json missing"
    assert (out / "api" / "catalog.json").is_file(), "catalog.json missing"
    assert (out / "api" / "composites.json").is_file(), "composites.json missing"
    assert (out / "api" / "registry.json").is_file(), "registry.json missing"
    assert (out / "assets" / "snapshot-readonly.css").is_file(), \
        "snapshot-readonly.css not in bundle"

    # inputs/<inv>.json present for each investigation
    isets = json.loads((out / "api" / "investigation-summaries.json").read_text())["investigations"]
    assert len(isets) >= 1, "investigation-summaries.json is empty for v2e-invest"
    for inv in isets:
        inv_name = inv["name"]
        assert (out / "api" / "inputs" / f"{inv_name}.json").is_file(), \
            f"api/inputs/{inv_name}.json missing"

    # investigation-summaries parity
    orig_ws = server.WORKSPACE
    server.WORKSPACE = _V2E_INVEST
    server._WP_CACHE.clear()
    try:
        expected_isets = json.loads(json.dumps(
            {"investigations": server._build_iset_summary_for_test(_V2E_INVEST)},
            default=server._json_default,
        ))
    finally:
        server.WORKSPACE = orig_ws
        server._WP_CACHE.clear()
    assert json.loads((out / "api" / "investigation-summaries.json").read_text()) == expected_isets, \
        "investigation-summaries.json parity failed for v2e-invest"

    # ── Full-surface golden (Tasks 1-5) ─────────────────────────────────────────
    # Simulations DB + Visualizations/Analyses exports
    assert (out / "api" / "simulations.json").is_file(), "api/simulations.json missing"
    sims_data = json.loads((out / "api" / "simulations.json").read_text())
    assert "simulations" in sims_data, "api/simulations.json missing 'simulations' key"

    assert (out / "api" / "visualization-classes.json").is_file(), \
        "api/visualization-classes.json missing"
    viz_data = json.loads((out / "api" / "visualization-classes.json").read_text())
    assert "classes" in viz_data, "api/visualization-classes.json missing 'classes' key"

    # Composite-state dir exists (may be empty if no composites resolvable)
    assert (out / "api" / "composite-state").is_dir(), \
        "api/composite-state/ dir missing"

    # Snapshot banner in home shell
    home_html = (out / "index.html").read_text()
    assert "snapshot-banner" in home_html, "index.html missing #snapshot-banner"
    assert "snapshot-repo-label" in home_html, "index.html missing #snapshot-repo-label"

    # Repo switcher hidden by CSS in snapshot mode
    css_text = (out / "assets" / "snapshot-readonly.css").read_text()
    assert "#viv-workspace-switcher" in css_text, \
        "snapshot-readonly.css missing switcher hide rule"
    assert "#snapshot-repo-label" in css_text, \
        "snapshot-readonly.css missing repo-label show rule"


# ---------------------------------------------------------------------------
# Snapshot embed staging — figures referenced by study-detail embed_visualizations
# must be COPIED into the bundle and base-path-prefixed, or the iframes 404.
# ---------------------------------------------------------------------------

def test_stage_embed_visualizations_copies_and_prefixes(tmp_path):
    from vivarium_dashboard.publish import _stage_embed_visualizations

    ws = tmp_path / "ws"
    fig = ws / "reports" / "figures" / "my-study"
    fig.mkdir(parents=True)
    (fig / "1-fig+plus.html").write_text("<html>fig</html>")  # literal '+' in name

    out = tmp_path / "bundle"
    out.mkdir()

    spec = {
        "embed_visualizations": [
            {"name": "1-fig+plus", "url": "/reports/figures/my-study/1-fig+plus.html"},
            {"name": "missing", "url": "/reports/figures/my-study/absent.html"},
            {"name": "api", "url": "/api/study/x.json"},
            {"name": "ext", "url": "https://example.com/x.html"},
        ]
    }
    _stage_embed_visualizations(spec, ws, out, "/v2ecoli/dashboard")

    # present file: copied into the bundle at the same rel path + url prefixed
    staged = out / "reports" / "figures" / "my-study" / "1-fig+plus.html"
    assert staged.is_file()
    assert staged.read_text() == "<html>fig</html>"
    assert spec["embed_visualizations"][0]["url"] == \
        "/v2ecoli/dashboard/reports/figures/my-study/1-fig+plus.html"
    # missing source: left as-is (no crash), api/external: untouched
    assert spec["embed_visualizations"][1]["url"] == "/reports/figures/my-study/absent.html"
    assert spec["embed_visualizations"][2]["url"] == "/api/study/x.json"
    assert spec["embed_visualizations"][3]["url"] == "https://example.com/x.html"


def test_stage_embed_visualizations_no_base_path(tmp_path):
    """With no base path (root hosting), files still copy but URLs stay root-absolute."""
    from vivarium_dashboard.publish import _stage_embed_visualizations
    ws = tmp_path / "ws"
    fig = ws / "reports" / "figures" / "s"
    fig.mkdir(parents=True)
    (fig / "f.html").write_text("x")
    out = tmp_path / "b"; out.mkdir()
    spec = {"embed_visualizations": [{"url": "/reports/figures/s/f.html"}]}
    _stage_embed_visualizations(spec, ws, out, "")
    assert (out / "reports" / "figures" / "s" / "f.html").is_file()
    assert spec["embed_visualizations"][0]["url"] == "/reports/figures/s/f.html"


def test_build_bundle_shell_embeds_are_staged_and_prefixed(tmp_workspace, tmp_path):
    """End-to-end: a reports/figures/<study>/ figure must be copied into the
    bundle AND its URL base-path-prefixed in BOTH the study JSON and the
    server-rendered per-study shell (the <iframe src>). Regression for the
    study-detail 'Embedded visualizations' 404 under a hosting base path."""
    from vivarium_dashboard import publish

    fig = server.WORKSPACE / "reports" / "figures" / "alpha"
    fig.mkdir(parents=True)
    (fig / "f1.html").write_text("<html><body>fig one</body></html>")

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out, base_path="/v2ecoli/dashboard")

    # file copied into the bundle at the same workspace-relative path
    assert (out / "reports" / "figures" / "alpha" / "f1.html").is_file()
    # study JSON URL prefixed
    j = json.loads((out / "api" / "study" / "alpha.json").read_text())
    urls = [e["url"] for e in j.get("embed_visualizations", [])]
    assert "/v2ecoli/dashboard/reports/figures/alpha/f1.html" in urls
    # per-study SHELL iframe src prefixed (not root-absolute /reports/...)
    shell = (out / "studies" / "alpha" / "index.html").read_text()
    assert 'src="/v2ecoli/dashboard/reports/figures/alpha/f1.html"' in shell
    assert 'src="/reports/figures/alpha/f1.html"' not in shell


# ---------------------------------------------------------------------------
# Analyses-tab saved-visualizations export (parsimony 3D gallery)
# ---------------------------------------------------------------------------

def _pbg_parsimony_available() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("pbg_parsimony") is not None
    except Exception:
        return False


@pytest.fixture
def ws_with_saved_pack(tmp_workspace):
    """Add a stub study with a saved parsimony 3D pack (+ meta + meshes) to the
    shared workspace. The pack's mesh LOD urls are workspace-rooted-relative
    (as pbg-parsimony writes them)."""
    sd = server.WORKSPACE / "studies" / "x" / "viz" / "3d"
    sd.mkdir(parents=True)
    # A stub study.yaml (no variants/composite) — exists only to host viz assets.
    (server.WORKSPACE / "studies" / "x" / "study.yaml").write_text(
        yaml.safe_dump({"name": "x", "description": "3d stub"})
    )
    pack = {
        "format": "parsimony.pack.v1",
        "ingredients": [{
            "id": 0, "name": "thing",
            "shape": {"kind": "mesh", "lods": [
                {"url": "studies/x/viz/3d/meshes/thing.lod0.obj", "voxel_size": 16.0},
                {"url": "studies/x/viz/3d/meshes/thing.lod1.obj", "voxel_size": 8.0},
            ]},
        }],
        "placements": [],
    }
    (sd / "scene.pack.json").write_text(json.dumps(pack))
    (sd / "scene.meta.json").write_text(json.dumps(
        {"ingredients": {"thing": {"display_name": "Thing", "count": 5}}}
    ))
    (sd / "meshes").mkdir()
    (sd / "meshes" / "thing.lod0.obj").write_text("o thing0\n")
    (sd / "meshes" / "thing.lod1.obj").write_text("o thing1\n")
    return server.WORKSPACE


@pytest.mark.skipif(not _pbg_parsimony_available(),
                    reason="pbg_parsimony not installed")
def test_build_bundle_exports_saved_visualizations(ws_with_saved_pack, tmp_path):
    """build_bundle with a base path writes the saved-visualizations API JSON,
    copies the parsimony viewer assets, copies the pack + meta + meshes
    preserving the studies/<name>/viz/3d path, and rewrites the copied pack's
    mesh urls to be base-path-prefixed (so the viewer's resolveMeshUrl, which
    prepends '/', resolves them under the hosting base path)."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out, base_path="/v2ecoli/dashboard/")

    # api/saved-visualizations.json lists the stub study's pack.
    sv = json.loads((out / "api" / "saved-visualizations.json").read_text())
    assert sv["parsimony_available"] is True
    saved = sv["saved"]
    assert any(e["study"] == "x" for e in saved)
    entry = next(e for e in saved if e["study"] == "x")
    # pack_url/meta_url stay workspace-rooted-absolute; the frontend prefixes base.
    assert entry["pack_url"] == "/studies/x/viz/3d/scene.pack.json"
    assert entry["meta_url"] == "/studies/x/viz/3d/scene.meta.json"

    # Parsimony viewer assets copied.
    assert (out / "parsimony-viewer" / "index.html").is_file()
    assert (out / "parsimony-viewer" / "viewer.js").is_file()

    # Pack + meta + meshes copied preserving the studies/<name>/viz/3d path.
    dst_pack = out / "studies" / "x" / "viz" / "3d" / "scene.pack.json"
    assert dst_pack.is_file()
    assert (out / "studies" / "x" / "viz" / "3d" / "scene.meta.json").is_file()
    assert (out / "studies" / "x" / "viz" / "3d" / "meshes" / "thing.lod0.obj").is_file()

    # Copied pack's mesh urls are base-path-prefixed (no leading slash → the
    # viewer's resolveMeshUrl prepends '/' → /v2ecoli/dashboard/studies/...obj).
    pdata = json.loads(dst_pack.read_text())
    lods = pdata["ingredients"][0]["shape"]["lods"]
    assert lods[0]["url"] == "v2ecoli/dashboard/studies/x/viz/3d/meshes/thing.lod0.obj"
    assert lods[1]["url"] == "v2ecoli/dashboard/studies/x/viz/3d/meshes/thing.lod1.obj"
    assert not lods[0]["url"].startswith("/")


@pytest.mark.skipif(not _pbg_parsimony_available(),
                    reason="pbg_parsimony not installed")
def test_build_bundle_saved_viz_root_hosting(ws_with_saved_pack, tmp_path):
    """With an empty base path (root hosting) the copied pack's mesh urls stay
    workspace-rooted-relative (resolveMeshUrl → /studies/...obj at the root)."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out, base_path="")

    dst_pack = out / "studies" / "x" / "viz" / "3d" / "scene.pack.json"
    pdata = json.loads(dst_pack.read_text())
    assert pdata["ingredients"][0]["shape"]["lods"][0]["url"] == \
        "studies/x/viz/3d/meshes/thing.lod0.obj"


# ---------------------------------------------------------------------------
# Notebook export — publish hook (follow-up to the notebook_export feature)
# ---------------------------------------------------------------------------

def test_build_bundle_exports_investigation_notebooks(tmp_workspace, tmp_path):
    """build_bundle ships a runnable notebook + .py per investigation and a
    manifest, without mutating the (parity-checked) iset payloads."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    summary = publish.build_bundle(server.WORKSPACE, out)

    for inv_name in summary["investigations"]:
        ipynb = out / "investigation-notebooks" / f"{inv_name}.ipynb"
        py = out / "investigation-notebooks" / f"{inv_name}.py"
        assert ipynb.is_file(), f"missing {ipynb}"
        assert py.is_file(), f"missing {py}"
        nb = json.loads(ipynb.read_text())
        assert nb["nbformat"] == 4 and nb["cells"]

    # the manifest lists every exported notebook with bundle-relative urls
    manifest = json.loads((out / "api" / "investigation-notebooks.json").read_text())
    by_slug = {n["slug"]: n for n in manifest["notebooks"]}
    assert set(summary["investigations"]) <= set(by_slug)
    for inv_name in summary["investigations"]:
        assert by_slug[inv_name]["ipynb"] == f"investigation-notebooks/{inv_name}.ipynb"
        assert by_slug[inv_name]["py"] == f"investigation-notebooks/{inv_name}.py"

    # the published iset JSON stays byte-parity with the live builder (no mutation)
    iset = json.loads((out / "api" / "investigation" / "main-inv.json").read_text())
    assert "notebook" not in iset

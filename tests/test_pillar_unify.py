from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_workbench/templates/study-detail.html").read_text()
JS = (ROOT / "vivarium_workbench/static/study-detail.js").read_text()


def test_merged_panels_exist_and_old_gone():
    # 5-tab study layout: Simulations stays a panel; the Visualizations panel is
    # gone (its gallery folds into Overview). The older split wrappers stay gone.
    assert 'id="panel-simulate"' in HTML and 'data-kind="simulate"' in HTML
    assert 'id="panel-visualize"' not in HTML and 'data-kind="visualize"' not in HTML
    for old in ['id="panel-simulations"', 'id="panel-runs"', 'id="panel-observables"', 'id="panel-visualizations"']:
        assert old not in HTML, f"old wrapper still present: {old}"


def test_single_member_buttons():
    # 5-tab study layout: exactly one `.study-pillar` button for simulate; the
    # visualize pillar is gone (figures folded into Overview).
    import re
    btns = re.findall(r'<button class="study-pillar[^"]*" data-kind="simulate"[^>]*>', HTML)
    assert len(btns) == 1, f"simulate: expected 1 pillar button, got {len(btns)}"
    assert 'data-kind="visualize"' not in HTML
    for old in ["_setStudyTab('simulations')", "_setStudyTab('observables')", "_setStudyTab('visualize')"]:
        assert old not in HTML


def _panel(idattr):
    i = HTML.index(idattr)
    nxt = HTML.find('class="study-tab-panel"', i + 10)
    return HTML[i: nxt if nxt != -1 else len(HTML)]


def test_inner_hooks_preserved():
    # 5-tab study layout: readouts fold into Model (compose); the charts +
    # native gallery fold into Overview; the runs table stays in Simulations.
    compose = _panel('id="panel-compose"')
    assert 'readouts-table' in compose
    overview = _panel('id="panel-overview"')
    assert 'viz-charts-panel' in overview
    sim = _panel('id="panel-simulate"')
    assert 'id="panel-runs"' not in HTML  # wrapper gone; runs render via the Sim-DB table:
    assert 'study-sim-table' in sim


def test_other_panels_untouched():
    for k in ["overview", "compose", "audit", "conclusions"]:
        assert f'id="panel-{k}"' in HTML


def test_readouts_and_visualize_load_their_own_content():
    # 5-tab study layout: the Model tab loads the readouts table; the Overview
    # tab loads the figure charts (both re-homed from their old tabs). The
    # per-kind dispatch branches for the removed tabs are gone.
    i = JS.index("function _setStudyTab")
    block = JS[i:i + 1200]
    assert "kind === 'compose'" in block and "_loadReadouts()" in block
    assert "kind === 'overview'" in block and "_loadCharts('viz-charts-panel')" in block
    # old single-kind loaders gone
    assert "kind === 'visualize'" not in JS
    assert "kind === 'readouts'" not in JS
    assert "kind === 'visualizations'" not in JS
    assert "kind === 'observables'" not in JS


def test_callers_repointed():
    assert "_setStudyTab('runs')" not in JS
    assert "_setStudyTab('visualizations')" not in JS


# ---------------------------------------------------------------------------
# Task V2 (Fable §4.5): the native gallery + inline charts sources render the
# same `.figure-card` shell (template already gives the embed source that
# class), with a caption row (muted source chip, title, and a run-link
# rendered conditionally on run_id). Card markup is built client-side, so
# these assert against the JS source rather than served HTML.

def test_native_gallery_card_uses_figure_card_and_conditional_run_link():
    i = JS.index("function _loadNativeGallery")
    # Window spans the whole _loadNativeGallery body (it ends where _loadResults
    # begins, ~offset 3031). Kept generous so a graceful-degradation guard added
    # ahead of the card-building `.then` can't push the asserted markup out of view.
    block = JS[i:JS.index("function _loadResults", i)]
    assert '"figure-card"' in block
    assert 'font-weight:600;font-size:0.92em' not in block  # old bold native label gone
    assert 'figure-caption-row' in block and 'figure-source-chip' in block
    assert 'figure-run-link' in block and 'data-run-id' in block
    # build_study_native_gallery returns one run_id for the whole gallery;
    # the caption is built conditionally so a study with no completed run
    # (run_id is None) omits the link instead of fabricating one.
    assert 'runId\n' in block or 'var runCaption = runId' in block
    assert '_wireFigureRunLinks(host)' in block


def test_chart_card_uses_figure_card_not_chart_card_box():
    i = JS.index("function _renderChartCard")
    block = JS[i:i + 1800]
    assert '"figure-card"' in block
    assert 'class="chart-card"' not in block
    assert 'figure-caption-row' in block and 'figure-source-chip' in block
    # V3 threads run_id into chart-sourced figures next; V2 already renders
    # the slot conditionally so it lights up without another JS change.
    assert 'c.run_id' in block


def test_loadcharts_wires_figure_run_links():
    i = JS.index("function _loadCharts")
    # Bound to the _loadCharts function body (up to the next top-level function)
    # rather than a fixed char window — the body grew when the per-run hub landed,
    # pushing the wire call past the old 2600-char window though it's still inside.
    end = JS.index("\n  function ", i + 1)
    block = JS[i:end]
    assert '_wireFigureRunLinks(panel)' in block


# ---------------------------------------------------------------------------
# Task V6: a declared threejs:/html: figure (study_charts.
# discover_declared_figure_charts) carries an `iframe_url` instead of
# img/svg. _renderChartCard must render it as an IFRAME figure-card — not an
# <img> — reusing the embed_visualizations iframe's trust model (a plain
# same-origin `src` iframe, no `sandbox` attribute) and the same auto-height
# onload resizer. A record with `c.img` (static image) must still render an
# <img>, not an iframe — no regression on the existing static-image path.
# ---------------------------------------------------------------------------

def test_chart_card_renders_iframe_for_declared_interactive_figure():
    i = JS.index("function _renderChartCard")
    block = JS[i:i + 1800]
    # A declared interactive figure renders as an iframe by EITHER c.iframe_url
    # (workspace-served, live dashboard) OR c.srcdoc (self-contained + inlined
    # Plotly, static publish snapshot — publish._inline_declared_iframe_figures).
    assert 'c.iframe_url' in block
    assert 'c.srcdoc' in block
    assert "'<iframe " in block
    assert 'figure-media-frame' in block
    # Same trust model as the embed_visualizations iframe: no `sandbox` attribute
    # beyond what embeds already use (srcdoc carries the doc inline but is
    # otherwise the identical iframe embed).
    assert 'sandbox=' not in block
    # Title/name is escaped before landing in the iframe's `title` attribute
    # and the caption row's figure-title span.
    assert 'escapeHtmlForTests(title)' in block
    assert 'escapeHtmlForTests(c.title)' in block


def test_chart_card_static_image_path_unchanged():
    i = JS.index("function _renderChartCard")
    block = JS[i:i + 1800]
    # The img branch (PNG/GIF) is still reachable and unconditioned on
    # iframe_url — a static image record still renders <img>, not an iframe.
    assert "'<img class=\"chart-img figure-media\"" in block


DS_JS = (ROOT / "vivarium_workbench/static/data-source.js").read_text()


def test_native_gallery_fetch_goes_through_datasource_for_snapshot():
    # Regression: the Visualizations-tab native gallery (and the Overview key
    # figure) must resolve their URL through DataSource.nativeGalleryUrl(), not a
    # raw "/api/study-native-gallery/<slug>" fetch. In a published snapshot the
    # live route is absent and the panels live at <base>/api/study-native-gallery/
    # <slug>.json — a raw .json-less fetch 404s and the tab shows "No figures yet."
    # despite published panels (what this guards against).
    assert "'/api/study-native-gallery/' + encodeURIComponent" not in JS, \
        "native gallery is fetched raw; route it through DataSource.nativeGalleryUrl()"
    assert JS.count("DataSource.nativeGalleryUrl(") >= 2, \
        "both _loadNativeGallery and _loadOverviewKeyFigure must use nativeGalleryUrl()"

    # DataSource exposes the helper, and it appends .json (+ base path) in
    # snapshot mode — byte-parity with publish.py's api/study-native-gallery/.
    assert "nativeGalleryUrl:" in DS_JS, "DataSource must export nativeGalleryUrl"
    i = DS_JS.index("function _nativeGalleryUrl")
    block = DS_JS[i:i + 400]
    assert '"snapshot"' in block and '+ ".json"' in block, \
        "nativeGalleryUrl must map to the baked .json in snapshot mode"
    assert "_base() +" in block, "nativeGalleryUrl must honour the snapshot base path"

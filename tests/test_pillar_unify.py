from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_workbench/templates/study-detail.html").read_text()
JS = (ROOT / "vivarium_workbench/static/study-detail.js").read_text()


def test_merged_panels_exist_and_old_gone():
    assert 'id="panel-simulate"' in HTML and 'data-kind="simulate"' in HTML
    assert 'id="panel-visualize"' in HTML and 'data-kind="visualize"' in HTML
    for old in ['id="panel-simulations"', 'id="panel-runs"', 'id="panel-observables"', 'id="panel-visualizations"']:
        assert old not in HTML, f"old wrapper still present: {old}"


def test_single_member_buttons():
    import re
    for p in ("simulate", "visualize"):
        btns = re.findall(r'<button class="study-tab"[^>]*data-pillar="%s"[^>]*>' % p, HTML)
        assert len(btns) == 1, f"{p}: expected 1 member button, got {len(btns)}"
    for old in ["_setStudyTab('simulations')", "_setStudyTab('observables')"]:
        assert old not in HTML


def _panel(idattr):
    i = HTML.index(idattr)
    nxt = HTML.find('class="study-tab-panel"', i + 10)
    return HTML[i: nxt if nxt != -1 else len(HTML)]


def test_inner_hooks_preserved():
    viz = _panel('id="panel-visualize"')
    assert 'readouts-table' in viz and 'viz-charts-panel' in viz
    sim = _panel('id="panel-simulate"')
    assert 'panel-runs' not in HTML  # wrapper gone, but runs content present:
    assert 'id="run-' in sim or 'runs-table' in sim or 'No runs yet' in sim


def test_other_panels_untouched():
    for k in ["overview", "compose", "tests", "conclusions"]:
        assert f'id="panel-{k}"' in HTML


def test_visualize_loads_both_readouts_and_charts():
    i = JS.index("function _setStudyTab")
    block = JS[i:i + 600]
    assert "kind === 'visualize'" in block
    assert "_loadReadouts()" in block and "_loadCharts('viz-charts-panel')" in block
    # old single-kind loaders gone
    assert "kind === 'visualizations'" not in JS
    assert "kind === 'observables'" not in JS


def test_callers_repointed():
    assert "_setStudyTab('runs')" not in JS
    assert "_setStudyTab('visualizations')" not in JS

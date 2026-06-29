# tests/test_compose_unification.py
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_dashboard/templates/study-detail.html").read_text()


def test_panel_compose_exists_and_old_wrappers_gone():
    assert 'data-kind="compose" id="panel-compose"' in HTML
    for old in ['id="panel-build"', 'id="panel-baseline"', 'id="panel-variants"', 'id="panel-interventions"']:
        assert old not in HTML, f"old wrapper still present: {old}"


def test_single_compose_member_button():
    import re
    compose_btns = re.findall(r'<button class="study-tab"[^>]*data-pillar="compose"[^>]*>', HTML)
    assert len(compose_btns) == 1, f"expected 1 compose member button, got {len(compose_btns)}"
    assert 'data-kind="compose" data-pillar="compose"' in HTML
    for old in ["_setStudyTab('build')", "_setStudyTab('baseline')", "_setStudyTab('variants')", "_setStudyTab('interventions')"]:
        assert old not in HTML, f"old compose tab button call still present: {old}"


def _panel_compose():
    i = HTML.index('id="panel-compose"')
    nxt = HTML.find('class="study-tab-panel"', i + 10)
    return HTML[i: nxt if nxt != -1 else len(HTML)]


def test_inner_hooks_preserved_in_compose():
    p = _panel_compose()
    assert "baseline-entry" in p and "btn-run-baseline" in p          # baseline Run/Remove
    assert "data-editable-intervention" in p                          # interventions editor
    assert "data-baseline-name" in p
    # Build block guard + not-v3 guard both present inside the merged panel
    assert "study.model_change or study.implementation_requirements" in p
    assert "not _is_v3" in p


def test_other_panels_untouched():
    for k in ["overview", "simulations", "observables", "runs", "tests", "visualizations", "conclusions"]:
        assert f'id="panel-{k}"' in HTML, f"unrelated panel disturbed: panel-{k}"

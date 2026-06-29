# tests/test_study_tabs_structure.py
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_dashboard/templates/study-detail.html").read_text()


def test_five_pillar_buttons_present():
    for p in ["understand", "inquire", "compose", "simulate", "visualize"]:
        assert f'data-pillar="{p}"' in HTML and f"_setStudyPillar('{p}')" in HTML


def test_subnav_container_present():
    assert 'id="study-subnav"' in HTML


def test_every_study_tab_button_has_a_pillar():
    # no member button left without data-pillar (rough check: count study-tab buttons
    # with onclick=_setStudyTab vs those carrying data-pillar)
    import re
    btns = re.findall(r'<button class="study-tab"[^>]*onclick="_setStudyTab\([^<]*</button>', HTML)
    assert btns, "expected member buttons"
    for b in btns:
        assert "data-pillar=" in b, f"member button missing data-pillar: {b[:80]}"


def test_panels_unchanged_all_eleven_present():
    for kind in ["overview", "build", "simulations", "baseline", "observables",
                 "variants", "interventions", "runs", "tests", "visualizations", "conclusions"]:
        assert f'data-kind="{kind}"' in HTML and f'id="panel-{kind}"' in HTML


def test_deep_link_onclicks_preserved():
    assert "_setStudyTab('tests')" in HTML or "_setStudyTab(\\'tests\\'" in HTML
    assert "_setStudyTab('conclusions')" in HTML or "_setStudyTab(\\'conclusions\\'" in HTML


def test_js_has_pillar_switcher():
    js = (ROOT / "vivarium_dashboard/static/study-detail.js").read_text()
    assert "function _setStudyPillar" in js
    assert "window._setStudyPillar" in js
    assert "dataset.pillar" in js or "data-pillar" in js
    assert "study-pillar" in js          # toggles the pillar buttons

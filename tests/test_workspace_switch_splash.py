"""Item 70 phase 3 — local in-place workspace switch: a branded splash covers
the post-reload boot gap. No real intermediate stages exist for an in-place
switch (session_env.prepare returns ready immediately), so this is
deliberately NOT a ProgressTrack stages view — just a subtle-pulse logo,
dismissed by the rail's first real post-reload fetch resolving, never a timer.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
CSS = (ROOT / "vivarium_workbench/static/progress-track.css").read_text()
WSP_JS = (ROOT / "vivarium_workbench/static/workspace-picker.js").read_text()
WALK_JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def test_splash_script_is_the_first_thing_in_body():
    body_i = HTML.index("<body>")
    layout_i = HTML.index('<div class="viv-layout">')
    between = HTML[body_i:layout_i]
    assert "sessionStorage.getItem('viv-switch-splash')" in between
    assert "sessionStorage.removeItem('viv-switch-splash')" in between
    assert 'el.className = \'viv-switch-splash\'' in between
    # picks the already-established dark/light logo pair, not a new asset
    assert "vivarium-logo" in between
    assert "vivarium-logo-dark" not in between  # built via string concat, not literal


def test_splash_css_present_no_fabricated_stages():
    assert ".viv-switch-splash {" in CSS
    assert "@keyframes viv-switch-pulse" in CSS
    assert "prefers-reduced-motion" in CSS.split("@keyframes viv-switch-pulse", 1)[1][:400]
    # confirm no ProgressTrack stages markup was (mis)used for this splash
    assert "mode: \"stages\"" not in CSS


def test_switch_sets_splash_flag_before_reload_only_on_success():
    i = WSP_JS.index('fetch("/api/source/switch"')
    block = WSP_JS[i:i + 900]
    assert 'sessionStorage.setItem("viv-switch-splash"' in block
    # flag-set must be inside the success (r.ok) branch, before reload — not
    # unconditional, and not on the network-failure catch.
    ok_i = block.index("r.ok")
    flag_i = block.index('sessionStorage.setItem("viv-switch-splash"')
    reload_i = block.index("location.reload()")
    assert ok_i < flag_i < reload_i
    catch_i = block.index(".catch(")
    assert flag_i < catch_i  # the flag-set line is not inside the catch handler


def test_rail_refresh_returns_its_promise_for_the_dismiss_hook():
    i = WALK_JS.index("function _vivRefreshInvestigationsRail")
    body = WALK_JS[i:i + 1400]
    assert "return Promise.all([p1, p2])" in body


def test_splash_dismissed_by_real_fetch_not_a_timer():
    i = WALK_JS.index("Populate the Investigations rail section")
    block = WALK_JS[i:i + 700]
    assert "var _railReady = _vivRefreshInvestigationsRail()" in block
    assert "getElementById('viv-switch-splash')" in block
    assert "ready.then(dismiss)" in block
    assert "setTimeout" not in block

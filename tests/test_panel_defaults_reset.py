"""Both tool panels (chat + process-code) default to docked-right and closed. The
default only applies when nothing is stored, so browsers from earlier builds keep a
remembered left/open state. panel-dock.js clears those keys ONCE (flag-guarded) so
the new defaults take effect, then lets later manual choices persist. Static
assertions (no browser) — they guard the mechanism.
"""
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vivarium_workbench" / "static"
TEMPLATE = Path(__file__).parent.parent / "vivarium_workbench" / "templates" / "index.html.j2"


def test_one_time_reset_clears_the_remembered_panel_state():
    js = (STATIC / "panel-dock.js").read_text()
    assert "viv.panels.reset.v1" in js                     # flag so it runs exactly once
    for key in ("viv.ai.dock", "viv.ai.open", "viv.code.dock", "viv.code.open"):
        assert key in js, f"reset must clear {key}"
    assert "removeItem" in js


def test_defaults_are_right_and_closed():
    chat = (STATIC / "chat.js").read_text()
    assert "lsGet('viv.ai.dock', 'right')" in chat         # chat docks right by default
    assert "lsGet('viv.ai.open', '0')" in chat             # chat closed by default
    code = (STATIC / "process-code.js").read_text()
    assert "defaultDock: 'right'" in code                  # code docks right by default
    pd = (STATIC / "panel-dock.js").read_text()
    assert "lsGet(K.open, '0')" in pd                      # panels (incl. code) closed by default


def test_reset_runs_before_the_panels_read_storage():
    t = TEMPLATE.read_text()
    assert t.index("assets/panel-dock.js") < t.index("assets/chat.js")
    assert t.index("assets/panel-dock.js") < t.index("assets/process-code.js")

"""Guard: the Resources tab's in-browser file viewer stays wired.

Pure client JS (no JS test harness here), so this asserts the viewer function and
its wiring into all three resource renderers are present in walkthrough.js — a
regression (removed / un-wired) fails CI instead of silently shipping download-only.
"""
from pathlib import Path

WALK = Path(__file__).resolve().parents[1] / "vivarium_workbench" / "static" / "walkthrough.js"


def test_resource_viewer_present_and_wired():
    js = WALK.read_text(encoding="utf-8")
    # the viewer + its helpers exist
    for sym in ("_openResourceModal", "_inputsViewBtn", "_inputsFileUrl", "window._viewResource"):
        assert sym in js, f"resource viewer symbol missing: {sym}"
    # a View button is wired into each of the three resource renderers
    assert js.count("_inputsViewBtn(") >= 3, "View button not wired into all three resource lists"
    # snapshot mode still resolves via the GitHub-raw inputs base (not a dead '/path')
    assert "inputsDownloadBase" in js


def test_pdf_and_text_both_handled():
    js = WALK.read_text(encoding="utf-8")
    assert "res-view-frame" in js          # PDF -> iframe
    assert "_RES_TEXT_EXT" in js           # text extensions -> fetched <pre>
    assert "res-view-note" in js           # other types -> graceful fallback


SD = Path(__file__).resolve().parents[1] / "vivarium_workbench" / "static" / "study-detail.js"


def test_view_fetch_uses_cors_enabled_raw_host():
    # The snapshot's github.com/<o>/<r>/raw/… base 302-redirects to
    # raw.githubusercontent.com and drops CORS on the redirect, so a fetch of it
    # fails. The viewer must rewrite to the raw host (which sends CORS) before fetch.
    js = WALK.read_text(encoding="utf-8")
    assert "raw.githubusercontent.com" in js
    assert "fetch(srcUrl)" in js


def test_model_antimony_open_by_default():
    # The Antimony source shows expanded by default on the Model page (still
    # collapsible), not hidden behind a collapsed summary.
    js = SD.read_text(encoding="utf-8")
    assert 'details open class="mdef-ant"' in js


STYLE = Path(__file__).resolve().parents[1] / "vivarium_workbench" / "static" / "style.css"


def test_model_antimony_pane_is_resizable():
    # The Antimony pane is user-resizable (drag to enlarge), not a fixed slice.
    css = STYLE.read_text(encoding="utf-8")
    i = css.find(".mdef-ant-pre{")
    block = css[i:i + 400]
    assert "resize:vertical" in block or "resize: vertical" in block

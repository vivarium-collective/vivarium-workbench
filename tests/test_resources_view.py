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

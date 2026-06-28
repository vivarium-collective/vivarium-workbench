"""Task 6: Verify that configure-run.js is loaded and ConfigureRun.mount is called
in the correct context in both templates."""
from pathlib import Path
from vivarium_dashboard import server


def _read(rel):
    return (Path(server.__file__).parent / rel).read_text(encoding="utf-8")


def test_widget_script_loaded_and_mounted():
    idx = _read("templates/index.html.j2")
    assert "configure-run.js" in idx                       # script included
    assert "ConfigureRun.mount" in idx                     # explorer + list mount
    assert 'target: "adhoc"' in idx or "target:'adhoc'" in idx
    sd = _read("templates/study-detail.html")
    assert "ConfigureRun.mount" in sd                      # study Runs tab
    assert 'target: "study"' in sd or "target:'study'" in sd


def test_composites_list_has_configure_and_run_action():
    """I3: both grid and list views in walkthrough.js carry a 'Configure & Run' button."""
    wt = _read("static/walkthrough.js")
    assert "Configure &amp; Run" in wt, (
        "walkthrough.js composite cards must render a 'Configure & Run' button"
    )

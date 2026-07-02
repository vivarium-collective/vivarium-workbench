"""Verify configure-run.js is loaded only where needed (study-detail),
and that composite cards carry a single Explore button (Tasks 9 & 10 revamp)."""
from pathlib import Path
import vivarium_workbench


def _read(rel):
    return (Path(vivarium_workbench.__file__).parent / rel).read_text(encoding="utf-8")


def test_widget_script_loaded_in_study_detail():
    """configure-run.js is still loaded (and mounted) in study-detail for study runs."""
    sd = _read("templates/study-detail.html")
    assert "ConfigureRun.mount" in sd                      # study Runs tab
    assert 'target: "study"' in sd or "target:'study'" in sd


def test_widget_script_not_in_composite_explorer():
    """configure-run.js must NOT be loaded in index.html.j2 (Tasks 9 & 10 removed it)."""
    idx = _read("templates/index.html.j2")
    assert "ce-configure-run" not in idx
    assert "_ceShowPanel" not in idx


def test_composites_list_has_single_explore_action():
    """I3 (revised): both grid and list views carry exactly one Explore button; no Configure & Run."""
    wt = _read("static/walkthrough.js")
    assert "_openCompositeExplorer" in wt, (
        "walkthrough.js composite cards must render an Explore button"
    )
    assert "_openCompositeConfigureRun" not in wt, (
        "walkthrough.js must not reference the retired _openCompositeConfigureRun"
    )
    assert "Configure &amp; Run" not in wt, (
        "walkthrough.js composite cards must not render a 'Configure & Run' button"
    )

from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def test_dag_node_click_selects_then_opens():
    """Two-stage DAG interaction: one click SELECTS a study (highlight + expand
    in place at readable bands); a second click on the already-selected study
    OPENS it. The old inline ↗ open button is gone. Still no double-click handler
    and no quick-look side-card drawer (both removed)."""
    assert "node.onclick = _aigNodeClick" in JS           # single handler now
    assert "window._aigSelected" in JS                    # selection state tracked
    assert "node.classList.add('aig-node-selected')" in JS
    assert "_openStudyInsideInvestigation(s.name)" in JS  # second click opens
    assert "_toggleExpand" in JS                          # expand helper still used
    # the standalone ↗ open-study button was removed
    assert "aig-open-study" not in JS
    # no double-click handler
    assert "node.ondblclick" not in JS
    # the graph no longer opens the quick-look side-card drawer
    assert "_openInvestigationDrawer('study', s)" not in JS

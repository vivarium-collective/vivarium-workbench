from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def test_dag_node_click_selects_then_opens():
    """Two-stage DAG interaction: one click SELECTS a study (highlight + expand
    in place at readable bands); a second click on the already-selected study
    OPENS it. The "show full card" affordance now EXPANDS the card IN PLACE (the
    full Asks/Finds/plan grow downward from the card's anchored top) — the old
    floating pop-out / quick-look side-card drawer are both gone, and so is any
    double-click handler."""
    assert "node.onclick = _aigNodeClick" in JS           # single handler now
    assert "window._aigSelected" in JS                    # selection state tracked
    assert "node.classList.add('aig-node-selected')" in JS
    assert "_openStudyInsideInvestigation(s.name)" in JS  # second click opens
    assert "_toggleExpandInPlace" in JS                   # in-place expand handler
    # "Open study →" now lives INLINE inside the expanded detail block (not a
    # floating pop-out, not a drawer) — so the button is present, but the pop-out
    # modal code path is gone.
    assert "aig-full-details" in JS                       # inline expansion block
    assert "aig-open-study" in JS                         # open-study button is inline
    assert "_aigShowCardPopout" not in JS                 # floating pop-out removed
    assert "aig-card-popout" not in JS                    # ...and its modal element
    # no double-click handler
    assert "node.ondblclick" not in JS
    # the graph no longer opens the quick-look side-card drawer
    assert "_openInvestigationDrawer('study', s)" not in JS

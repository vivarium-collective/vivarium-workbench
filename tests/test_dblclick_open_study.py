from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def test_dag_node_click_expands_and_opens_via_button():
    """A DAG node single-click toggles expand/collapse; the full study view is
    opened from the node's inline "open" button, which stops propagation so the
    open click doesn't also toggle the node. Still no double-click handler and
    no quick-look side-card drawer (both removed)."""
    # node click toggles expand/collapse
    assert "node.onclick = _toggleExpand" in JS
    # the full study is opened from the inline button, guarded against bubbling
    # up to the node's expand toggle
    assert "event.stopPropagation(); _openStudyInsideInvestigation(" in JS
    # no double-click handler
    assert "node.ondblclick" not in JS
    # the graph no longer opens the quick-look side-card drawer
    assert "_openInvestigationDrawer('study', s)" not in JS

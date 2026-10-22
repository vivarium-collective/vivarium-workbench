"""The investigation graph fits the whole network to the window on first paint.

The initial auto-fill (`_aigAutoFillInline`) used to fit the view zoom to the
pane HEIGHT only, so a WIDE multi-study chain overflowed horizontally and looked
zoomed-in until you hit the Fit button. It now fits BOTH dimensions (like Fit),
so the whole graph is visible on first paint.

walkthrough.js isn't node-requirable as a whole (needs a full DOM), so this is
verified by marker presence — mirrors tests/test_investigation_graph_orientation.py.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def autofill_fn():
    """The _aigAutoFillInline function body, up to its export."""
    i = JS.index("function _aigAutoFillInline()")
    j = JS.index("window._aigAutoFillInline = _aigAutoFillInline;", i)
    return JS[i:j]


def test_initial_autofill_fits_both_width_and_height():
    fn = autofill_fn()
    # It must consult the canvas WIDTH and the pane width, not only the height.
    assert "dataset.canvasW" in fn
    assert "clientWidth" in fn
    # The fit zoom is the min of the height-fit and the width-fit, so a wide
    # network shows the whole graph rather than overflowing horizontally.
    assert "Math.min(zH, (w - 16) / cw)" in fn

"""The workbench embed layer neutralizes the Plotly height:100% anti-pattern.

Recurring bug: Plotly's write_html(full_html=True) injects
`html,body{height:100%}`, so an embedded chart's body FILLS the figure iframe and
scrollHeight reports the iframe's own (growing) height — an ever-growing band of
whitespace below the plot. The durable fix lives HERE, in `_fitFigureFrame`
(shared by the SPA render and the server-rendered embeds), so it works for ANY
workspace's charts without each one fixing its own write_html or regenerating:
force html+body to height:auto, then measure the true content extent.

study-detail.js isn't node-requirable as a whole (needs a DOM), so — like the
other frontend tests — we assert on the source markers.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDY_JS = (ROOT / "vivarium_workbench/static/study-detail.js").read_text()


def test_fit_forces_body_height_auto():
    # the neutralization: html+body are forced to height:auto so a height:100%
    # body shrinks to its content instead of filling the iframe.
    assert "b.style.height = 'auto'" in STUDY_JS
    assert "e.style.height = 'auto'" in STUDY_JS


def test_fit_measures_children_extent_not_documentelement_scrollheight():
    # content height comes from the body children's rendered extent...
    assert "getBoundingClientRect().bottom" in STUDY_JS
    assert "_measureFigureHeight" in STUDY_JS
    # ...and NOT from documentElement.scrollHeight (reports the viewport minimum).
    assert "e.scrollHeight" not in STUDY_JS


def test_fit_still_honors_a_deliberate_pinned_pixel_height():
    # a figure that pins a fixed body height with hidden overflow is left as-is.
    assert "pinnedH" in STUDY_JS

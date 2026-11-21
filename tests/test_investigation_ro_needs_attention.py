"""Read-only investigation fixes: the Needs-attention tab + the Shortcuts menu.

Two RO bugs reported on the published viva-genesis workbench:

1. The **Needs attention** tab hung forever on "Scanning…". Its loader did a raw
   ``fetch('/api/needs-attention?investigation=…')`` — a query-param route with no
   ``.json`` in a static bundle, so it 404'd offline, and the failure path left the
   spinner in place. Fix: publish bakes ``api/needs-attention/<slug>.json`` and the
   loader goes through ``DataSource.loadNeedsAttention`` (snapshot-aware), clearing
   the spinner on any failure.

2. The **Shortcuts ▾** dropdown (the rightmost toolbar action) opened with
   ``left:0`` and overflowed off the right edge of the viewport. Fix: right-align it
   (``right:0;left:auto``) so it opens leftward and stays on screen.

Static assertions over the source (no browser).
"""
from pathlib import Path

import vivarium_workbench


def _static(name: str) -> str:
    return (Path(vivarium_workbench.__file__).parent / "static" / name).read_text(
        encoding="utf-8"
    )


def test_data_source_exposes_snapshot_aware_needs_attention_loader():
    ds = _static("data-source.js")
    # the URL helper maps snapshot -> baked .json, live -> query endpoint
    assert "function _needsAttentionUrl(" in ds
    assert '"/api/needs-attention/" + encodeURIComponent(slug) + ".json"' in ds
    assert '"/api/needs-attention?investigation=" + encodeURIComponent(slug)' in ds
    # and it is exposed as a public DataSource method
    assert "async loadNeedsAttention(" in ds


def test_needs_attention_loader_uses_datasource_and_clears_spinner():
    js = _static("walkthrough.js")
    anchor = js.index("function _renderInvNeedsAttention(")
    end = js.index("window._renderInvNeedsAttention =", anchor)
    body = js[anchor:end]
    # snapshot-aware load (not a bare /api/needs-attention fetch as the only path)
    assert "window.DataSource.loadNeedsAttention(name)" in body, (
        "needs-attention must load via DataSource.loadNeedsAttention so the baked "
        ".json resolves offline"
    )
    # the spinner is cleared on failure/empty — the tab must never hang on "Scanning…"
    assert "Needs-attention scan unavailable." in body
    # both the empty-data branch and the .catch clear it
    assert body.count("_clear(") >= 2


def test_shortcuts_menu_is_right_aligned_not_clipped():
    js = _static("walkthrough.js")
    anchor = js.index('id="inv-report-menu"')
    decl = js[anchor:anchor + 220]
    assert "right:0;left:auto" in decl, (
        "the investigation Shortcuts menu must right-align (right:0;left:auto) so "
        "it opens leftward and does not overflow the viewport's right edge"
    )
    assert "position:absolute;left:0;top:100%" not in decl, (
        "the old left:0 anchoring (menu extends off the right edge) must be gone"
    )

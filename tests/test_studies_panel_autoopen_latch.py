"""The Studies panel auto-opens ONCE when studies first appear, then never
re-opens on later programmatic rail re-renders.

Regression: clicking "Run" in the Discovery cycle panel refreshes the
investigations rail (`_vivRefreshDiscoveryViews` -> `_openInvestigationDetail` /
`_vivRefreshInvestigationsRail`). That rail is `#viv-rail-investigations`, which
studies-panel.js watches with a MutationObserver -> onListRender -> applyDefault.
Before the latch, applyDefault re-opened the Studies dock on EVERY re-render
(closing-by-navigation never sets the userset key), so a Run popped the Studies
panel back open over the user's work. The one-shot `autoOpened` latch fixes it.

Static assertions (no browser) — they guard the mechanism in the source.
"""
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vivarium_workbench" / "static"


def test_auto_open_is_latched_to_fire_once():
    js = (STATIC / "studies-panel.js").read_text()
    # A one-shot latch variable exists and gates the auto-open.
    assert "autoOpened" in js, "the one-shot auto-open latch must exist"
    # applyDefault opens only when the latch is still unset, and sets it.
    assert "if (!autoOpened) { autoOpened = true; if (!ctl.isOpen()) ctl.open(); }" in js, (
        "auto-open must be guarded by the !autoOpened latch and set it on first open"
    )
    # The bare, un-latched re-open must be gone.
    assert "if (hasStudies()) { if (!ctl.isOpen()) ctl.open(); }" not in js, (
        "the un-latched auto-open (re-opens on every rail re-render) must be removed"
    )


def test_run_refresh_path_still_targets_the_watched_rail():
    # Guards the causal chain the latch defends against: the discovery refresh
    # re-renders #viv-rail-investigations, which is exactly LIST_ID in the panel.
    panel = (STATIC / "studies-panel.js").read_text()
    assert "LIST_ID = 'viv-rail-investigations'" in panel
    wt = (STATIC / "walkthrough.js").read_text()
    assert "_vivRefreshDiscoveryViews" in wt
    assert "_vivRefreshInvestigationsRail" in wt

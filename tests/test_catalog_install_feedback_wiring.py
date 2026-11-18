"""Wiring: the catalog-install flow shows in-page progress and non-blocking
results instead of a silent gap after the confirm + a blocking alert() (issue
#1618, bug 7). Vanilla-JS frontend with no JS runner, so — like the other
*_wiring tests — assert the shipped asset.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _install_fn() -> str:
    js = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()
    i = js.index("function _proceedWithCatalogInstall")
    return js[i: js.index("window._proceedWithCatalogInstall", i)]


def test_install_shows_persistent_progress_toast():
    fn = _install_fn()
    # A progress toast is raised before the POST and kept until we clear it
    # (durationMs:0), so the long clone + pip install isn't a silent gap.
    assert "Installing ' + name" in fn
    assert "durationMs: 0" in fn
    assert "_clearProgress" in fn


def test_install_results_are_non_blocking_toasts():
    fn = _install_fn()
    # Progress is cleared, then errors (install + network) surface via a danger
    # toast rather than a blocking alert() — which also unblocks automation.
    assert "_clearProgress();" in fn
    assert fn.count("{ danger: true }") >= 2


def test_install_surfaces_rollback_note():
    fn = _install_fn()
    # A rolled-back install tells the user nothing was left staged.
    assert "json.rolled_back" in fn
    assert "rolled back" in fn.lower()

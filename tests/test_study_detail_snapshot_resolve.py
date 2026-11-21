"""Regression: study-detail.js must detect snapshot mode race-free.

The body.snapshot class is only added on DOMContentLoaded (walkthrough.js), so a
resolve that fires during initial render can read it as false and fall through to
the LIVE /api/...?query route, which 404s in a static bundle -> the Model tab
showed 'Could not resolve "<composite>"'. The fix mirrors configure-run.js: prefer
the authoritative __DASH_CONFIG__.mode signal (set synchronously inline) with the
body class as a fallback, via a shared _isSnapshot() helper used at every
static-vs-live URL decision.
"""
from pathlib import Path

import vivarium_workbench


def _js() -> str:
    return (Path(vivarium_workbench.__file__).parent / "static" / "study-detail.js").read_text(
        encoding="utf-8"
    )


def test_isSnapshot_helper_defined_with_config_fallback():
    js = _js()
    assert "function _isSnapshot()" in js
    # authoritative signal + body-class fallback, same as configure-run.js
    assert "__DASH_CONFIG__ && window.__DASH_CONFIG__.mode === 'snapshot'" in js
    assert "document.body.classList.contains('snapshot')" in js


def test_composite_resolve_and_assurance_urls_use_the_helper():
    js = _js()
    # the three static-vs-live URL decisions route through _isSnapshot()
    assert "var _cfgUrl = _isSnapshot()" in js       # Model tab: per-baseline config
    assert "var _mcUrl = _isSnapshot()" in js        # Model tab: composite cards
    assert "return _isSnapshot()" in js              # assurance tabs (Tests/Audit/Build)
    # and each still selects the baked path form in snapshot mode
    assert "/api/composite-resolve/' + encodeURIComponent" in js


def test_model_definition_panel_resolve_uses_the_helper():
    """The Model tab's Composition panel (_renderModelDefinition, the #1682
    Model Definition card) must also route its composite-resolve fetch through
    _isSnapshot() — otherwise it hits the live ?id= route, 404s in a static
    bundle, and the Composition panel renders blank (while the Loom sub-pane,
    which already branched here, still works)."""
    js = _js()
    assert "var _mdUrl = _isSnapshot()" in js
    # live ?id= route must not be the UNCONDITIONAL head of this fetch
    assert "fetch('/api/composite-resolve?id=' + encodeURIComponent(compositeId))" not in js


def test_no_bare_body_class_check_gates_a_resolve_url():
    """The bare body.snapshot check must appear ONLY inside _isSnapshot() — never
    as the head of a URL decision, which is exactly the race that 404'd."""
    js = _js()
    assert js.count("document.body.classList.contains('snapshot')") == 1

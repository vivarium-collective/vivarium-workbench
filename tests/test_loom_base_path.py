"""Base-path shim injection for third-party bundles (bigraph-loom).

Regression cover for: the loom's bundled JS calls a root-absolute
``/api/composite-test-run``; under ``serve --base-path`` that escaped the prefix
and, in the co-tenant ALB deployment, was routed to sms-api (404).
"""
from __future__ import annotations

from vivarium_workbench.lib.report import (
    _apply_live_base_path,
    _base_path_shim,
    inject_base_path_shim,
)


def test_shim_is_injected_before_the_bundle_scripts():
    """Must land inside <head> and BEFORE module scripts, so fetch is patched first."""
    html = (
        "<html><head><title>loom</title></head>"
        "<body><script type='module' src='./assets/index.js'></script></body></html>"
    )
    out = inject_base_path_shim(html, "/workbench")
    assert out.index("__BASE_PATH__") < out.index("</head>")
    assert out.index("__BASE_PATH__") < out.index("assets/index.js")


def test_noop_without_a_base_path():
    """Local dev serves at root — the loom HTML must be untouched."""
    html = "<html><head></head><body>x</body></html>"
    assert inject_base_path_shim(html, "") == html
    assert inject_base_path_shim("", "/workbench") == ""


def test_shim_covers_the_loom_api_call_and_binds_the_prefix():
    shim = _base_path_shim("/workbench")
    assert '"/api/"' in shim          # the prefix list that catches /api/composite-test-run
    assert '"/workbench"' in shim     # bound to this deployment's base path


def test_inject_falls_back_when_there_is_no_head():
    out = inject_base_path_shim("<div>x</div>", "/workbench")
    assert out.startswith("<script>") and "<div>x</div>" in out


def test_apply_live_base_path_still_injects_the_shim():
    """Regression: extracting _base_path_shim must not change the SPA path."""
    html = (
        "<html><head></head><body>"
        '<script>window.__DASH_CONFIG__ = { mode: "local-server" };</script>'
        "</body></html>"
    )
    out = _apply_live_base_path(html, "/workbench")
    assert 'basePath: "/workbench"' in out
    assert "__BASE_PATH__" in out

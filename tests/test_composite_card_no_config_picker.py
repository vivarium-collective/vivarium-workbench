"""#1113 (functional half): when a Cloud image dispatch fails closed with
409 reason=no-config-for-composite (composite_test_run_views._dispatch_build_
image_run, #1127), the composite card and Composite Explorer must give the
user a way to actually dispatch -- render the response's own available_configs
as a picker and retry the SAME request with config_filename pinned -- rather
than leaving them stuck on a bare error.

Structural (text) assertions against the served JS source, matching this
repo's existing convention for vanilla-JS behavior (no bundler/JS test
runner -- see test_composite_run_pinned_confirm.py / test_remote_run_panel.py).
"""
from __future__ import annotations

from pathlib import Path

import vivarium_workbench

_STATIC = Path(vivarium_workbench.__file__).parent / "static"


def _js(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def _function_body(js: str, name: str) -> str:
    """Slice out one top-level `function <name>(` definition by locating the
    next top-level `function ` after it (robust to this file's size -- no
    hardcoded line ranges)."""
    start = js.index("function " + name + "(")
    rest = js[start + 1:]
    nxt = rest.index("\n  function ")
    return js[start:start + 1 + nxt]


def test_config_picker_helper_defined():
    js = _js("walkthrough.js")
    assert "function _renderConfigPicker(" in js
    picker = _function_body(js, "_renderConfigPicker")
    assert "cfg-picker-select" in picker
    assert "cfg-picker-go" in picker
    assert "onDispatch(sel.value)" in picker


def test_run_composite_offers_picker_and_retries_with_config_filename():
    js = _js("walkthrough.js")
    fn = _function_body(js, "_runComposite")
    assert "no-config-for-composite" in fn
    assert "available_configs" in fn
    assert "_renderConfigPicker(" in fn
    # The retry path must thread the picked config back into the SAME request.
    assert "config_filename: configFilename" in fn


def test_ce_test_run_offers_picker_and_retries_with_config_filename():
    js = _js("walkthrough.js")
    fn = _function_body(js, "_ceTestRun")
    assert "no-config-for-composite" in fn
    assert "available_configs" in fn
    assert "_renderConfigPicker(" in fn
    assert "reqBody.config_filename = configFilename" in fn

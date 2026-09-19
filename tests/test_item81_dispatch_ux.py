"""Backlog item 81: replace the native confirm()/alert() dialogs ahead of a
real AWS Batch dispatch with styled equivalents (folded in from Eran's video
review, item 80), and restyle item 6's shipped ASCII chain-progress bar
("doesn't meaningfully show anything" -- Alex, live, item 80) into a real
DOM progress bar.

Structural (text) assertions against the served JS source, matching this
repo's existing convention for vanilla-JS behavior (no bundler/JS test
runner -- see test_composite_run_pinned_confirm.py / test_remote_run_panel.py).
"""
from __future__ import annotations

import re
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


def _strip_line_comments(js: str) -> str:
    return "\n".join(re.sub(r"//.*$", "", line) for line in js.splitlines())


def test_dispatch_remote_pinned_has_no_native_dialogs():
    js = _js("study-detail.js")
    fn = _strip_line_comments(_function_body(js, "_dispatchRemotePinned"))
    # A real call, not a mention in a comment -- never preceded by "_" (our
    # own _confirmModal/_alertModal).
    assert not re.search(r"(?<!_)\bconfirm\(", fn)
    assert not re.search(r"(?<!_)\balert\(", fn)
    assert "_confirmModal(" in fn
    assert "_alertModal(" in fn


def test_alert_modal_defined_single_button_non_blocking():
    js = _js("study-detail.js")
    assert "function _alertModal(" in js
    fn = _function_body(js, "_alertModal")
    # Real DOM overlay, not window.alert() -- same non-blocking-event-loop
    # rationale as _confirmModal (item 20b).
    assert "document.createElement('div')" in fn
    assert "cancelBtn" not in fn  # pure acknowledgement, no Cancel choice


def test_chain_progress_bar_is_real_dom_not_ascii_text():
    js = _js("study-detail.js")
    fn = _function_body(js, "_renderChainProgress")
    # The old bar built a literal block-character string ('█'/'░') --
    # confirm that's gone, replaced by real DOM segment elements.
    assert "█" not in fn
    assert "░" not in fn
    assert "chain-progress-seg-done" in fn
    assert "chain-progress-seg-fail" in fn
    assert "chain-progress-seg-live" in fn


def test_chain_progress_el_builds_segmented_track_once():
    js = _js("study-detail.js")
    fn = _function_body(js, "_chainProgressEl")
    assert "chain-progress-track" in fn
    assert "chain-progress-seg-done" in fn
    assert "transition:width" in fn

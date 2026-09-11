"""Item 20b: `_dispatchRemoteComposite`'s AWS-Batch confirmation must be a
real, non-blocking DOM modal (`_confirmModal`), not `window.confirm()`.

`confirm()`/`alert()`/`prompt()` are the only web-platform APIs that
synchronously freeze the page's whole JS event loop -- including whatever a
browser-automation tool injects to read or screenshot the page -- which made
this exact dialog impossible to drive through Claude-in-Chrome during the
2026-09-11 CD2 Vignette-1 UI-verification push (three real attempts hung on
this call). `_confirmModal` keeps item 20a's own safety property (a human
must see the resolved simulator_id/mechanism/params and explicitly click
before real AWS spend happens) via a plain DOM overlay instead, which never
blocks the event loop.

No JS test runner in this repo (vanilla JS, no bundler) -- source-level
assertions against the served JS, matching this project's own established
convention (test_remote_run_panel.py, test_composite_run_pinned_confirm.py,
test_remote_dispatch_param_editing.py).
"""
from __future__ import annotations

from pathlib import Path

import vivarium_workbench

_STATIC = Path(vivarium_workbench.__file__).parent / "static"


def _js(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def _dispatch_remote_composite_block(js: str) -> str:
    i = js.index("function _dispatchRemoteComposite()")
    j = js.index("window._dispatchRemoteComposite = _dispatchRemoteComposite;", i)
    return js[i:j]


def _confirm_modal_block(js: str) -> str:
    i = js.index("function _confirmModal(message)")
    # Next top-level `function ` after it -- robust to file growth, no
    # hardcoded line range (mirrors test_composite_run_pinned_confirm.py's
    # own _function_body slicer).
    rest = js[i + 1:]
    j = rest.index("\n  function ")
    return js[i:i + 1 + j]


def test_confirm_modal_defined_and_promise_based():
    js = _js("study-detail.js")
    assert "function _confirmModal(message)" in js
    block = _confirm_modal_block(js)
    assert "new Promise(function (resolve)" in block
    # Cancel, OK, Escape, and backdrop-click must all be real resolve paths --
    # a modal a user can't dismiss would be worse than the confirm() it
    # replaces.
    assert "resolve(result)" in block
    assert "done(false)" in block  # Cancel / Escape / backdrop
    assert "done(true)" in block  # OK


def test_confirm_modal_never_uses_innerhtml_for_the_message():
    """confirm() always rendered its message as plain text, never markup --
    the message embeds form values the user typed (variant/config_filename/
    raw extra-params JSON), so a modal built with innerHTML instead of
    textContent would introduce a real XSS regression that didn't exist
    before this fix."""
    block = _confirm_modal_block(_js("study-detail.js"))
    assert "text.textContent = message" in block
    # The word "innerHTML" may legitimately appear in an explanatory comment
    # (it does -- documenting why textContent was chosen instead); what must
    # never appear is an actual assignment to it.
    assert ".innerHTML =" not in block and ".innerHTML=" not in block


def test_dispatch_remote_composite_uses_async_modal_not_blocking_confirm():
    block = _dispatch_remote_composite_block(_js("study-detail.js"))
    assert "confirm(msg)" not in block
    assert "_confirmModal(msg)" in block


def test_dispatch_remote_composite_still_confirms_before_submit():
    """The core safety property item 20a added and this fix must preserve:
    the real POST only fires after the user has explicitly confirmed --
    never before, never unconditionally."""
    block = _dispatch_remote_composite_block(_js("study-detail.js"))
    i_confirm = block.index("_confirmModal(msg)")
    i_ok_check = block.index("if (!ok) return _CANCELLED;", i_confirm)
    i_post = block.index("/api/remote-run-submit", i_ok_check)
    assert i_confirm < i_ok_check < i_post

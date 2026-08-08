"""Item 20a: every live POST /api/composite-test-run launcher in walkthrough.js
(_runComposite's inline pcard Run bar, _ceTestRun's Composite Explorer Test Run
panel) must fetch the server-resolved remote-run-config and require explicit
confirmation before a remote-pinned deployment dispatches to AWS Batch —
mirrors study-detail.js's already-shipped _dispatchRemotePinned pattern
(fetch fresh, show repo/branch/commit/simulator_id, block on cancel).

Structural (text) assertions against the served JS source, matching this
repo's existing convention for vanilla-JS behavior (no bundler/JS test
runner — see test_configure_run_widget.py / test_remote_run_panel.py).
"""
from __future__ import annotations

from pathlib import Path

import vivarium_workbench

_STATIC = Path(vivarium_workbench.__file__).parent / "static"


def _js(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def _function_body(js: str, name: str) -> str:
    """Slice out one top-level `function <name>(` definition by locating the
    next top-level `function ` after it (robust to this file's size — no
    hardcoded line ranges)."""
    start = js.index("function " + name + "(")
    rest = js[start + 1:]
    nxt = rest.index("\n  function ")
    return js[start:start + 1 + nxt]


def test_shared_confirm_gate_defined_and_exported():
    js = _js("walkthrough.js")
    assert "function _confirmRemoteDispatchThen(" in js
    gate = _function_body(js, "_confirmRemoteDispatchThen")
    assert "/api/remote-run-config" in gate
    assert "cfg.pinned" in gate
    assert "confirm(" in gate
    assert "repo_url" in gate and "branch" in gate and "simulator_id" in gate
    assert "window._confirmRemoteDispatchThen = _confirmRemoteDispatchThen" in js


def test_run_composite_routes_through_confirm_gate_before_dispatch():
    """The pcard inline Run bar's ▶ Run button (_runComposite, bound via
    onclick="_runComposite(this)")."""
    js = _js("walkthrough.js")
    fn = _function_body(js, "_runComposite")
    assert "_confirmRemoteDispatchThen(" in fn
    assert "onclick=\"_runComposite(this)\"" in js
    # the actual dispatch must be INSIDE the gate's fire callback, not fired
    # unconditionally before it. rindex (not index): an existing explanatory
    # comment earlier in this function also mentions the endpoint string —
    # the REAL fetch() call is the LAST occurrence.
    assert fn.index("_confirmRemoteDispatchThen(") < fn.rindex("/api/composite-test-run")


def test_ce_test_run_routes_through_confirm_gate_before_dispatch():
    """The Composite Explorer's own Test Run panel (_ceTestRun)."""
    js = _js("walkthrough.js")
    fn = _function_body(js, "_ceTestRun")
    assert "_confirmRemoteDispatchThen(" in fn
    assert fn.index("_confirmRemoteDispatchThen(") < fn.index("/api/composite-test-run")

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


def test_composite_card_loom_run_routes_through_confirm_gate_before_dispatch():
    """The composite card's ▶ Run now lives in the loom's run bar (SetupRunPanel →
    api.ts `startRun`), which replaced the old inline pcard `_runComposite` button.
    `startRun` must gate a remote-pinned dispatch the same way — fetch fresh
    /api/remote-run-config, check `pinned`, confirm before POSTing composite-test-run
    (which routes to AWS Batch when pinned via resolve_run_target)."""
    api_ts = (Path(vivarium_workbench.__file__).parent
              / "loom" / "src" / "api.ts").read_text(encoding="utf-8")
    start = api_ts.index("export async function startRun(")
    # The gate call precedes the actual dispatch fetch inside startRun.
    fn = api_ts[start:start + 1 + api_ts[start + 1:].index("\nexport ")]
    assert "_confirmRemoteDispatch(" in fn
    assert fn.index("_confirmRemoteDispatch(") < fn.index("/api/composite-test-run")
    # And the gate itself reads the pinned config and confirms.
    assert "/api/remote-run-config" in api_ts
    assert "cfg.pinned" in api_ts
    assert "confirm(" in api_ts


def test_ce_test_run_routes_through_confirm_gate_before_dispatch():
    """The Composite Explorer's own Test Run panel (_ceTestRun)."""
    js = _js("walkthrough.js")
    fn = _function_body(js, "_ceTestRun")
    assert "_confirmRemoteDispatchThen(" in fn
    assert fn.index("_confirmRemoteDispatchThen(") < fn.index("/api/composite-test-run")

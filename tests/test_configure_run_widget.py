from pathlib import Path
import vivarium_workbench

def _js():
    return (Path(vivarium_workbench.__file__).parent / "static" / "configure-run.js").read_text(encoding="utf-8")

def test_configure_run_form_generation_present():
    js = _js()
    assert "window.ConfigureRun" in js
    assert "function mount" in js or "mount:" in js
    assert "_buildConfigForm" in js and "_collectOverrides" in js
    assert "/api/composite-resolve" in js
    # type-driven inputs: number for float/int, checkbox for bool, text for string
    assert "'number'" in js or '"number"' in js
    assert "checkbox" in js
    # handles null/empty parameters without crashing
    assert "parameters || {}" in js or "|| {}" in js
    # collects overrides with type casting
    assert "parseFloat" in js or "Number(" in js
    assert "parseInt" in js

def test_configure_run_routing_and_persist_present():
    js = _js()
    assert "_wireRun" in js
    # context-aware routing
    assert "/api/composite-test-run" in js          # ad-hoc
    assert "/api/study-run-baseline" in js or "/api/study-run-variant" in js  # study
    assert "_ctx()" in js or "ctxState" in js        # reads {target, study}
    assert "'study'" in js or '"study"' in js
    # durable persist actions
    assert "/api/save-run-as-variant" in js
    assert "/api/run-delete" in js
    # tolerant polling (WS1 pattern)
    assert "consecutiveErrors" in js or "setTimeout" in js
    assert ".catch(" in js


def test_configure_run_adhoc_confirms_before_pinned_dispatch():
    """Item 20a: _runAdhoc (the Composites-tab ad-hoc run path) must fetch the
    server-resolved remote-run-config and require explicit confirmation
    before a remote-pinned deployment's POST /api/composite-test-run fires —
    mirrors study-detail.js's _dispatchRemotePinned pattern (same message
    shape: repo/branch/commit/simulator id)."""
    js = _js()
    # _confirmRemoteDispatchMsg is the message-building helper, defined right
    # before _runAdhoc (which calls it); slice from there so both are covered.
    adhoc = js[js.index("function _confirmRemoteDispatchMsg("):js.index("function _runStudy(")]
    assert "/api/remote-run-config" in adhoc
    assert "cfg.pinned" in adhoc
    assert "confirm(" in adhoc
    assert "_confirmRemoteDispatchMsg(cfg)" in adhoc  # _runAdhoc actually calls it
    assert "repo_url" in adhoc and "branch" in adhoc and "simulator_id" in adhoc
    # The actual dispatch is wrapped in a _fire() helper, DECLARED (but not
    # yet invoked) before the remote-run-config gate; what must hold is that
    # the gate check precedes the INVOCATION "_fire();" (not the declaration
    # "function _fire()", which textually comes first regardless).
    assert adhoc.index("cfg.pinned") < adhoc.rindex("_fire();")

"""Regression guard for the Composites-tab "play" buttons (Run + Stop).

The Run (▶) and Stop (■) buttons in the Configure & Run widget only work if a
three-part contract holds, and each part can be broken by an unrelated refactor:

  1. the HTTP endpoints the buttons call stay registered, with the right method
     (POST /api/composite-test-run, GET …/status, POST …/stop);
  2. those endpoints' handler views stay importable and behave; and
  3. configure-run.js keeps rendering the buttons and wiring their onclick to
     those endpoints (POST run → read run_id → poll status → POST stop).

These tests fail loudly if any leg is removed or renamed, so the buttons can't
silently stop working. (The endpoints' full behaviour is covered by
test_composite_test_run_views_lib / test_run_stop / test_composite_stop_views_lib;
this file guards that the button→endpoint wiring stays intact.)
"""
import re
from pathlib import Path

import vivarium_workbench


def _app_src() -> str:
    return (Path(vivarium_workbench.__file__).parent / "api" / "app.py").read_text(encoding="utf-8")


def _js() -> str:
    return (Path(vivarium_workbench.__file__).parent / "static" / "configure-run.js").read_text(encoding="utf-8")


def _assert_route(src: str, method: str, path: str) -> None:
    """The route `path` is registered for `method` in app.py — either as a
    hand-written `@app.<method>(...)` decorator, or as a declarative
    `R("<method>", "<path>", ...)` entry in the thin-route table (see
    api/route_registrar.py; a pure lib delegator may live there instead of a
    hand-written def). Still fails loudly if the endpoint is removed or its
    method changes — only the registration *style* is now either/or.

    The path literal can appear more than once (e.g. also in the readonly
    mutation allowlist), so accept the route if ANY occurrence is preceded by
    the expected decorator; quote style may be single or double.
    """
    # Declarative thin-route-table entry: R('post', '/api/...'  (quote-agnostic).
    table = re.search(
        rf"""R\(\s*['"]{re.escape(method)}['"]\s*,\s*['"]{re.escape(path)}['"]""", src)
    if table:
        return
    # Hand-written decorator: the path literal preceded by @app.<method>(.
    for quote in ('"', "'"):
        literal = f"{quote}{path}{quote}"
        occurrences = [i for i in range(len(src)) if src.startswith(literal, i)]
        if occurrences and any(f"@app.{method}(" in src[max(0, i - 300):i] for i in occurrences):
            return
    raise AssertionError(
        f"button endpoint {path!r} ({method.upper()}) is neither a @app.{method}(...) "
        f"decorator nor an R(\"{method}\", ...) thin-route-table entry in app.py — the "
        f"button's endpoint or request method changed")


# --- 1. the endpoints the buttons call stay registered -----------------------

def test_run_button_endpoint_registered():
    """▶ Run POSTs /api/composite-test-run."""
    _assert_route(_app_src(), "post", "/api/composite-test-run")


def test_status_poll_endpoint_registered():
    """The Run flow polls GET /api/composite-run/{run_id}/status."""
    _assert_route(_app_src(), "get", "/api/composite-run/{run_id}/status")


def test_stop_button_endpoint_registered():
    """■ Stop POSTs /api/composite-run/{run_id}/stop."""
    _assert_route(_app_src(), "post", "/api/composite-run/{run_id}/stop")


# --- 2. the handler views stay importable and behave -------------------------

def test_button_handler_views_importable_and_sane(tmp_path):
    """Each button's handler view imports and returns a sane status on a
    trivial call — a hard guard that the route→view delegation isn't dead."""
    from vivarium_workbench.lib.composite_test_run_views import composite_test_run
    from vivarium_workbench.lib.composite_stop_views import stop_composite_run
    from vivarium_workbench.lib import composite_run_views  # noqa: F401 — import must not raise

    ws = tmp_path
    (ws / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    (ws / ".pbg").mkdir()

    # Run handler: missing id → 400 (proves it's callable and validates input).
    body, status = composite_test_run(ws, {})
    assert status == 400 and body.get("error") == "missing id"

    # Stop handler: unknown run → 404 (proves it's callable and resolves the db).
    from vivarium_workbench.lib.composite_runs import connect
    connect(ws / ".pbg" / "composite-runs.db").close()
    body, status = stop_composite_run(ws, "no-such-run")
    assert status == 404 and body.get("outcome") == "not_found"


# --- 3. the frontend keeps rendering + wiring the buttons --------------------

def test_run_button_rendered_and_wired():
    js = _js()
    assert "cfg-run-btn" in js                       # the ▶ Run button
    assert "_wireRun" in js                          # binds its onclick
    assert "/api/composite-test-run" in js           # POSTs the run endpoint
    assert "run_id" in js                            # reads the returned run id
    assert "/status" in js                           # then polls status


def test_stop_button_rendered_and_wired():
    js = _js()
    assert "cfg-stop-btn" in js                      # the ■ Stop button
    assert "_stopRun" in js                          # binds its onclick
    assert "/stop" in js and "composite-run/" in js  # POSTs the stop endpoint

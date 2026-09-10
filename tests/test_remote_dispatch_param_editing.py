"""Regression tests for the remote-dispatch param-editing fix.

Three real problems this closes:

  1. There was no UI (or API-level equivalent) path to set
     baseline[0].params — in particular n_generations/n_seeds — at all, once
     a study existed.
  2. `_dispatchRemotePinned` (study-detail.js) silently defaulted an unset
     n_generations/n_seeds to 1x1 (``params.n_generations || 1``) with zero
     warning — a real, expensive-to-discover correctness bug for a real AWS
     Batch dispatch.
  3. `remote_run_submit` (lib/remote_run_views.py) had the identical ``or 1``
     default server-side, so a client-only fix would not have closed the gap
     for any other caller of ``/api/remote-run-submit``.

Server-side behavior — the study-baseline-add/-remove round trip, the new
required-field validation on ``/api/remote-run-submit``, and (via a tiny
local stand-in for the genuinely-external sms-api network boundary) the
exact values reaching the outbound dispatch call — is exercised through the
real ``dashboard_client`` fixture: a real FastAPI server subprocess against a
real workspace on disk, per this project's own testing convention (see
tests/conftest.py's ``dashboard_client`` and test_study_runs.py's
``_over_real_http``-suffixed tests, which this file's naming mirrors).
Nothing here mocks study_baseline_add/study_baseline_remove or
remote_run_submit itself — the only thing ever faked is the literal outbound
HTTP call to sms-api, an external service, exactly like the project's own
existing SmsApiClient fakes in test_remote_run_views_lib.py.

The new client-side JS (the editable params form + Save button in
_renderModelConfig/_saveModelParams, and _dispatchRemotePinned's guard /
confirm() enrichment / stale-state re-fetch) has no JS test runner in this
repo (vanilla JS, no bundler — see CLAUDE.md's "no separate build step").
Those are covered as source-level assertions instead, matching this repo's
own established convention for testing static JS (test_remote_run_panel.py /
test_compose_unification.py).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

import vivarium_workbench

# ---------------------------------------------------------------------------
# Shared workspace builder — mirrors test_study_runs.py's own
# `_over_real_http` fixtures (workspace.yaml + .pbg/ + studies/<slug>/study.yaml).
# ---------------------------------------------------------------------------


def _make_ws(tmp_path: Path, *, ws_name: str, params: dict | None = None) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(f"name: {ws_name}\n")
    (ws / ".pbg").mkdir()
    sd = ws / "studies" / "demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "demo",
        "baseline": [{"name": "core", "composite": "pkg.composites.cell", "params": params or {}}],
    }))
    return ws


# ---------------------------------------------------------------------------
# 1. An explicit param value set via the new add-then-remove path (what
#    _saveModelParams does) survives a save + reload untouched, and a
#    sibling, untouched param is not silently wiped.
# ---------------------------------------------------------------------------


def test_edited_baseline_param_survives_save_and_reload_over_real_http(tmp_path, dashboard_client):
    ws = _make_ws(
        tmp_path, ws_name="param-edit-ws",
        params={"n_generations": 1, "n_seeds": 1, "temperature": 37},
    )
    client = dashboard_client(ws)

    # Mirrors _saveModelParams exactly: merge the edited field into a COPY of
    # the CURRENT full params (never a bare single-field payload — that would
    # silently wipe n_seeds/temperature), add under a fresh name, then remove
    # the old entry. There is no single "update in place" endpoint.
    merged = {"n_generations": 1, "n_seeds": 1, "temperature": 42}
    add_res = client.post("/api/study-baseline-add", json={
        "study": "demo", "name": "core-edited", "composite": "pkg.composites.cell", "params": merged,
    })
    assert add_res.status_code == 200, add_res.text
    remove_res = client.post("/api/study-baseline-remove", json={"study": "demo", "name": "core"})
    assert remove_res.status_code == 200, remove_res.text

    # "reload": re-fetch fresh study state the SAME way the client does
    # (window.DataSource.loadStudy -> GET /api/study/<slug>), not a raw file read.
    reloaded = client.get("/api/study/demo")
    assert reloaded.status_code == 200, reloaded.text
    baseline = reloaded.json()["baseline"]
    assert len(baseline) == 1
    assert baseline[0]["name"] == "core-edited"
    assert baseline[0]["params"] == {"n_generations": 1, "n_seeds": 1, "temperature": 42}


# ---------------------------------------------------------------------------
# 2. /api/remote-run-submit blocks with a clear, specific error when
#    n_generations/n_seeds are unset. No network fake needed here: the new
#    check short-circuits before any outbound sms-api call.
# ---------------------------------------------------------------------------


def test_remote_run_submit_blocks_with_clear_error_when_unset_over_real_http(
    tmp_path, dashboard_client, monkeypatch
):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv(
        "VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "https://github.com/vivarium-collective/v2ecoli")
    ws = _make_ws(tmp_path, ws_name="blocked-dispatch-ws")
    client = dashboard_client(ws)

    res = client.post("/api/remote-run-submit", json={"study": "demo", "simulator_id": 66})
    assert res.status_code == 400, res.text
    assert "num_generations" in res.json().get("error", "")

    res2 = client.post(
        "/api/remote-run-submit",
        json={"study": "demo", "simulator_id": 66, "num_generations": 5})
    assert res2.status_code == 400, res2.text
    assert "num_seeds" in res2.json().get("error", "")


# ---------------------------------------------------------------------------
# 3. Dispatch submits the CORRECT values when they ARE set. sms-api is a
#    genuinely external network service (identical in kind to "AWS Batch"
#    itself) — this project's own existing tests already fake exactly this
#    boundary (test_remote_run_views_lib.py's _FakeThinClient). Because
#    dashboard_client spawns a real subprocess, that in-process fake can't
#    cross the process boundary, so this stands up a tiny real local HTTP
#    server (stdlib only) speaking sms-api's actual wire shape and points
#    VIVA_API_BASE at it — a real end-to-end HTTP round trip proving the
#    literal bytes on the wire carry the right values, not an assumption
#    about what SmsApiClient does with them.
# ---------------------------------------------------------------------------


class _FakeSmsApiHandler(BaseHTTPRequestHandler):
    captured: dict | None = None

    def do_POST(self):  # noqa: N802 (stdlib-mandated method name)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path.startswith("/api/v1/simulations"):
            parsed = urlparse(self.path)
            type(self).captured = {
                "query": parse_qs(parsed.query),
                "body": json.loads(raw) if raw else None,
            }
            self._reply(200, {"database_id": 4242})
            return
        self._reply(404, {"error": "not found"})

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A002 (stdlib signature) — silence default access log
        pass


@pytest.fixture
def fake_sms_api():
    server = HTTPServer(("127.0.0.1", 0), _FakeSmsApiHandler)
    _FakeSmsApiHandler.captured = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}", _FakeSmsApiHandler
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_remote_run_submit_forwards_correct_values_over_real_http(
    tmp_path, dashboard_client, monkeypatch, fake_sms_api
):
    base_url, handler = fake_sms_api
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv(
        "VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "https://github.com/vivarium-collective/v2ecoli")
    monkeypatch.setenv("VIVA_API_BASE", base_url)
    ws = _make_ws(tmp_path, ws_name="dispatch-values-ws")
    client = dashboard_client(ws)

    res = client.post("/api/remote-run-submit", json={
        "study": "demo", "simulator_id": 66, "num_generations": 7, "num_seeds": 3,
    })
    assert res.status_code == 202, res.text
    assert res.json()["simulation_id"] == 4242

    assert handler.captured is not None
    query = handler.captured["query"]
    assert query["num_generations"] == ["7"]
    assert query["num_seeds"] == ["3"]
    assert query["simulator_id"] == ["66"]
    # No silent-default gap here (unlike n_generations/n_seeds above, this one
    # is genuinely optional): a caller with no opinion on config_filename gets
    # exactly the request this repo has always sent — sms-api applies its own
    # server-side default. See the dedicated test below for the explicit case.
    assert "simulation_config_filename" not in query


def test_remote_run_submit_forwards_config_filename_over_real_http(
    tmp_path, dashboard_client, monkeypatch, fake_sms_api
):
    """Real regression test for the config_filename gap: sms-api's own
    default (api_simulation_default.json) only exists in the public
    vEcoli-lineage repos, so any repo without it (confirmed for sms-ecoli via
    GitHub code search: zero hits, the file has never existed there) 404s on
    every dispatch unless the caller supplies a real filename explicitly —
    GET /api/v1/simulations/discovery lists the pinned commit's real options.
    Proves the literal bytes reach the wire as `simulation_config_filename`
    (sms-api's own real query-param name, confirmed against
    viva_api/api/routers/sms.py), not merely that SmsApiClient's own params
    dict looks right in isolation."""
    base_url, handler = fake_sms_api
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv(
        "VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "https://github.com/vivarium-collective/v2ecoli")
    monkeypatch.setenv("VIVA_API_BASE", base_url)
    ws = _make_ws(tmp_path, ws_name="config-filename-ws")
    client = dashboard_client(ws)

    res = client.post("/api/remote-run-submit", json={
        "study": "demo", "simulator_id": 66, "num_generations": 7, "num_seeds": 3,
        "config_filename": "mecillinam_wellmixed.json",
    })
    assert res.status_code == 202, res.text

    assert handler.captured is not None
    query = handler.captured["query"]
    assert query["simulation_config_filename"] == ["mecillinam_wellmixed.json"]


# ---------------------------------------------------------------------------
# Client-side JS source assertions (no JS test runner in this repo — see
# module docstring). Boundaries are sliced on unique, stable markers so the
# assertions stay tied to the actual functions under test, not the whole file.
# ---------------------------------------------------------------------------


def _js_text() -> str:
    return (Path(vivarium_workbench.__file__).parent / "static" / "study-detail.js").read_text(encoding="utf-8")


def _dispatch_remote_composite_block(js: str) -> str:
    i = js.index("function _dispatchRemoteComposite()")
    j = js.index("window._dispatchRemoteComposite = _dispatchRemoteComposite;", i)
    return js[i:j]


def test_dispatch_remote_composite_reads_and_forwards_config_filename():
    """Source-level check for the same config_filename gap the two real
    end-to-end tests above close — mirrors this repo's own established
    convention for testing static JS with no bundler/test runner (see module
    docstring). Confirms the advanced panel's new field is actually wired
    into the request, not just present as inert markup."""
    block = _dispatch_remote_composite_block(_js_text())
    assert "cp-config-filename" in block
    assert "config_filename: configFilename" in block


def _dispatch_remote_pinned_block(js: str) -> str:
    i = js.index("function _dispatchRemotePinned(cfg)")
    j = js.index("window._dispatchCurrentSpecBaseline = _dispatchCurrentSpecBaseline;", i)
    return js[i:j]


def _model_config_block(js: str) -> str:
    i = js.index("function _coerceParamValue(raw, type)")
    j = js.index("// Simulations tab:", i)
    return js[i:j]


def test_dispatch_remote_pinned_has_no_silent_default_fallback():
    block = _dispatch_remote_pinned_block(_js_text())
    assert "|| 1" not in block
    assert "params.n_generations || 1" not in block
    assert "params.n_seeds || 1" not in block


def test_dispatch_remote_pinned_blocks_when_generations_or_seeds_unset():
    block = _dispatch_remote_pinned_block(_js_text())
    assert "if (!numGenerations) missing.push('n_generations');" in block
    assert "if (!numSeeds) missing.push('n_seeds');" in block
    # Blocks BEFORE the confirm()/POST — never asks the user to confirm a
    # dispatch that's going to be rejected anyway.
    i_guard = block.index("missing.length")
    i_confirm = block.index("confirm(msg)")
    i_post = block.index("/api/remote-run-submit")
    assert i_guard < i_confirm < i_post
    assert "Cannot dispatch:" in block
    assert "Model tab" in block  # points the user at the new editable-params UI


def test_dispatch_remote_pinned_confirm_shows_resolved_generations_and_seeds():
    block = _dispatch_remote_pinned_block(_js_text())
    i_msg = block.index("var msg =")
    i_confirm = block.index("confirm(msg)")
    msg_block = block[i_msg:i_confirm]
    assert "generations:" in msg_block
    assert "seeds:" in msg_block
    assert "numGenerations" in msg_block and "numSeeds" in msg_block


def test_dispatch_remote_pinned_refetches_fresh_study_state_before_reading_params():
    """Stale-state re-fetch: window._study can be a stale in-memory copy
    fetched before a param edit landed server-side (a confirmed real failure
    mode — a tab left open across a baseline-param save re-dispatched the OLD
    params from memory even though study.yaml on disk was already correct).
    _dispatchRemotePinned must re-fetch via DataSource.loadStudy before
    reading baseline[0].params."""
    block = _dispatch_remote_pinned_block(_js_text())
    assert "window.DataSource.loadStudy(slug)" in block
    i_fetch = block.index("loadStudy(slug)")
    i_read = block.index("baseline[0] && baseline[0].params")
    assert i_fetch < i_read


def test_model_config_renders_editable_input_and_save_button_when_baseline_name_present():
    block = _model_config_block(_js_text())
    assert 'class="model-param-input"' in block
    assert "data-param-key=" in block
    assert 'class="action-btn model-config-save"' in block
    assert "Save parameter changes" in block
    assert "var editable = !!baselineName;" in block


def test_model_config_readonly_when_no_baseline_name():
    """The conditions-only fallback card (no study.baseline[] entry backing
    it) must stay read-only — editing it would have nowhere real to save to,
    exactly like its existing 'Set composite' control is also absent there."""
    block = _model_config_block(_js_text())
    assert "if (!editable) return;" in block


def test_save_model_params_merges_edited_fields_into_a_copy_of_current_params():
    js = _js_text()
    i = js.index("function _saveModelParams")
    j = js.index("function _renderModelConfig")
    fn = js[i:j]
    assert "Object.assign({}, overrides || {})" in fn
    assert "if (input.dataset.edited !== '1') return;" in fn
    assert "/api/study-baseline-add" in fn
    assert "/api/study-baseline-remove" in fn
    assert fn.index("/api/study-baseline-add") < fn.index("/api/study-baseline-remove")


def test_model_config_load_passes_baseline_name_only_when_composite_input_exists():
    js = _js_text()
    i = js.index("function _loadModelConfig")
    j = js.index("window._loadModelConfig", i)
    fn = js[i:j]
    assert "baseline-composite-input" in fn
    assert (
        "_renderModelConfig(mount, res.body.parameters, overrides, esc, composite, baselineName);"
        in fn
    )

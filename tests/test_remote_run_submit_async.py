"""Regression tests for backlog item 51 (remote-run-submit-client-timeout-
too-short-for-large-dispatch), candidate fix B.

Real, live bug found 2026-08-14 during a real 1000-seed x 10-generation
canonical dispatch on smscdk: viva-api's ``POST /api/v1/simulations`` used to
submit a chain-dispatch campaign's ``n_seeds * n_generations`` AWS Batch jobs
SYNCHRONOUSLY inside that one HTTP request (~15 minutes for 10,000 jobs, paced
~40/s server-side) -- ~30x this client's 30s timeout. The client gave up,
raised ``SmsApiError``, and (because ``remote_run_submit`` let it propagate
uncaught) the UI got a bare "internal server error" 500 while viva-api kept
dispatching real, expensive AWS Batch jobs in the background regardless --
inviting a well-intentioned retry that would have fired a second, duplicate,
paid campaign.

The real fix (viva-api PR, tracked separately with its own tests) makes
viva-api's chain-dispatch submission genuinely asynchronous server-side: the
slow per-seed submission loop moves off ``POST /api/v1/simulations``'s
response path onto a background task (the SAME ``LocalTaskService`` +
``JobId.local`` mechanism already used for multi-minute Docker image builds),
so the call now returns in seconds regardless of campaign size, and status is
tracked/pollable throughout.

This file covers vivarium-workbench's OWN half of that contract -- the two
things actually under this repo's control:

  1. ``remote_run_submit`` (lib/remote_run_views.py) does not itself do
     anything whose cost scales with campaign size -- num_seeds/num_generations
     are forwarded as plain query params, never iterated over locally, so a
     1000x10 request costs this repo nothing extra to relay. Proven here with
     an EXPLICIT wall-clock bound, not just "the test didn't hang" -- a
     regression tripwire if this repo ever grows its own O(campaign-size) work
     on this path.
  2. A genuinely-external ``SmsApiError`` (a real upstream 5xx, a dropped
     connection, or -- pre viva-api-fix -- a real timeout) now surfaces as a
     clean ``502 {"error": <real message>, "reachable": false}`` instead of
     FastAPI's generic unhandled-exception 500 (api/app.py's
     ``_unhandled_error_handler``, which replaces the real message with a bare
     "internal server error" -- exactly the opaque failure item 51 observed
     live). This is the load-bearing "the UI gets a real, accurate signal
     even on the failure path" half of the fix.

Proving viva-api's own campaign-scale submission loop is ACTUALLY
asynchronous (not just that this repo doesn't add extra delay of its own) is
out of scope for a workbench-only test -- that requires real AWS Batch
submission timing, covered by viva-api's own test suite instead. What IS
provable here, and is exactly what this repo owns: a real FastAPI server
subprocess (``inproc_client``, per this project's standing rule against
mocking the layer under test) talking real HTTP to a real local stand-in
server for the genuinely-external sms-api/viva-api boundary (mirroring
test_remote_dispatch_param_editing.py's own ``fake_sms_api`` pattern) shows
correct, fast, honestly-reported behavior end to end.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

# ---------------------------------------------------------------------------
# Minimal workspace builder -- mirrors test_remote_dispatch_param_editing.py's
# own `_make_ws` (same minimal shape: workspace.yaml + one study with a
# baseline entry, nothing more is needed for remote_run_submit's own guards).
# ---------------------------------------------------------------------------


def _make_ws(tmp_path: Path, *, ws_name: str) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(f"name: {ws_name}\n")
    (ws / ".pbg").mkdir()
    sd = ws / "studies" / "demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "demo",
        "baseline": [{"name": "core", "composite": "pkg.composites.cell", "params": {}}],
    }))
    return ws


# ---------------------------------------------------------------------------
# Real local stand-in for the genuinely-external sms-api/viva-api HTTP
# boundary (stdlib only) -- the only thing ever faked, exactly like
# test_remote_dispatch_param_editing.py's `_FakeSmsApiHandler`. Configurable
# per-test via class attributes so one handler covers the fast-ack, error,
# and status-transition scenarios below without three near-duplicate classes.
# ---------------------------------------------------------------------------


class _FakeSmsApiHandler(BaseHTTPRequestHandler):
    # Class-level knobs, reset per test via the `fake_sms_api` fixture.
    submit_status: int = 200
    submit_body: dict = {"database_id": 4242}
    status_sequence: list[dict] = []  # successive GET .../status responses
    status_calls: int = 0

    def do_POST(self):  # noqa: N802 (stdlib-mandated method name)
        length = int(self.headers.get("Content-Length") or 0)
        _ = self.rfile.read(length) if length else b""
        if self.path.startswith("/api/v1/simulations"):
            self._reply(type(self).submit_status, type(self).submit_body)
            return
        self._reply(404, {"error": "not found"})

    def do_GET(self):  # noqa: N802 (stdlib-mandated method name)
        parsed = urlparse(self.path)
        if parsed.path.endswith("/status") and "/api/v1/simulations/" in parsed.path:
            seq = type(self).status_sequence
            idx = min(type(self).status_calls, len(seq) - 1) if seq else 0
            type(self).status_calls += 1
            self._reply(200, seq[idx] if seq else {"status": "running"})
            return
        self._reply(404, {"error": "not found"})

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A002 (stdlib signature) -- silence default access log
        pass


@pytest.fixture
def fake_sms_api():
    server = HTTPServer(("127.0.0.1", 0), _FakeSmsApiHandler)
    _FakeSmsApiHandler.submit_status = 200
    _FakeSmsApiHandler.submit_body = {"database_id": 4242}
    _FakeSmsApiHandler.status_sequence = []
    _FakeSmsApiHandler.status_calls = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}", _FakeSmsApiHandler
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _pin(monkeypatch, base_url: str) -> None:
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv(
        "VIVARIUM_WORKBENCH_REMOTE_REPO_URL", "https://github.com/vivarium-collective/v2ecoli")
    monkeypatch.setenv("VIVA_API_BASE", base_url)


# ---------------------------------------------------------------------------
# 1. A real 1000x10 dispatch request (the exact shape of the incident) is
#    relayed and answered in seconds, not minutes -- an explicit wall-clock
#    bound, not just "didn't hang". sms-api itself acks instantly here (this
#    is what viva-api's own fix makes true for a real chain-dispatch
#    campaign too, regardless of size); what THIS test proves is that
#    vivarium-workbench adds no scaling cost of its own on top.
# ---------------------------------------------------------------------------


def test_remote_run_submit_returns_fast_for_large_chain_dispatch_over_real_http(
    tmp_path, inproc_client, monkeypatch, fake_sms_api
):
    base_url, _handler = fake_sms_api
    _pin(monkeypatch, base_url)
    ws = _make_ws(tmp_path, ws_name="large-dispatch-ws")
    client = inproc_client(ws)

    start = time.monotonic()
    res = client.post("/api/remote-run-submit", json={
        "study": "demo", "simulator_id": 66, "num_generations": 10, "num_seeds": 1000,
    })
    elapsed = time.monotonic() - start

    assert res.status_code == 202, res.text
    assert res.json()["simulation_id"] == 4242
    assert res.json()["phase"] == "running"
    # Generous bound for local-loopback + subprocess overhead -- orders of
    # magnitude below the old 30s client timeout, and far below the ~15
    # real minutes a 1000x10 campaign used to hold this request open for.
    assert elapsed < 5.0, f"remote-run-submit took {elapsed:.2f}s for a 1000x10 request -- expected a few seconds"


# ---------------------------------------------------------------------------
# 2. An upstream failure surfaces as a clean, informative 502 -- not a bare
#    "internal server error" 500 (api/app.py's generic Exception handler,
#    which is exactly what item 51's own observed incident hit: the SmsApiError
#    raised by the client used to propagate uncaught out of remote_run_submit).
# ---------------------------------------------------------------------------


def test_remote_run_submit_surfaces_upstream_error_as_502_not_generic_500_over_real_http(
    tmp_path, inproc_client, monkeypatch, fake_sms_api
):
    base_url, handler = fake_sms_api
    handler.submit_status = 500
    handler.submit_body = {"error": "simulated upstream failure"}
    _pin(monkeypatch, base_url)
    ws = _make_ws(tmp_path, ws_name="upstream-error-ws")
    client = inproc_client(ws)

    res = client.post("/api/remote-run-submit", json={
        "study": "demo", "simulator_id": 66, "num_generations": 3, "num_seeds": 2,
    })

    assert res.status_code == 502, res.text
    body = res.json()
    assert body["reachable"] is False
    # The REAL SmsApiError message reaches the caller -- not the generic
    # "internal server error" the old unguarded call would have produced.
    assert "internal server error" not in body["error"]
    assert "500" in body["error"]


# ---------------------------------------------------------------------------
# 3. End-to-end: submit acks fast with a real simulation_id, and polling that
#    id (the thin-client's own GET /api/remote-run-poll, exactly what the JS
#    panel calls) reflects real, changing status -- proving the full
#    "submit fast, poll for real progress" loop (not fire-and-forget with no
#    visibility) through actual workbench request-handling code.
# ---------------------------------------------------------------------------


def test_remote_run_submit_then_poll_reflects_real_status_transition_over_real_http(
    tmp_path, inproc_client, monkeypatch, fake_sms_api
):
    base_url, handler = fake_sms_api
    handler.status_sequence = [{"status": "running"}, {"status": "completed"}]
    _pin(monkeypatch, base_url)
    ws = _make_ws(tmp_path, ws_name="poll-transition-ws")
    client = inproc_client(ws)

    submit_res = client.post("/api/remote-run-submit", json={
        "study": "demo", "simulator_id": 66, "num_generations": 10, "num_seeds": 1000,
    })
    assert submit_res.status_code == 202, submit_res.text
    sim_id = submit_res.json()["simulation_id"]

    poll1 = client.get(f"/api/remote-run-poll?simulation_id={sim_id}")
    assert poll1.status_code == 200, poll1.text
    assert poll1.json()["phase"] == "running"

    poll2 = client.get(f"/api/remote-run-poll?simulation_id={sim_id}")
    assert poll2.status_code == 200, poll2.text
    assert poll2.json()["phase"] == "done"

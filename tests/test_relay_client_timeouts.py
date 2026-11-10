"""A relayed env-worker request must wait as long as it told viva-api it would.

``start_relayed_env_worker`` asks viva-api to wait up to ``accept_timeout`` (300 s by default) for a cold worker
pod to pull its image and dial back; ``call_relayed_env_worker`` forwards a per-call ``timeout``. But ``_post``
sent every request with the client's own default timeout (30-60 s), so the HTTP request was abandoned long before
viva-api had answered: a cold worker on the hosted test stack made the first Composites call fail with
"POST .../workers/relay failed (sms-api unreachable ...): timed out", although the worker came up fine minutes later.

The server here is a real HTTP server standing in for viva-api (it answers after a delay); what is under test,
the client's request timeout, is exercised for real over real sockets.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError

DELAY = 2.0          # how long the stand-in viva-api takes to answer a relay POST
CLIENT_DEFAULT = 0.5  # the client's default timeout: shorter than DELAY, as 30-60 s is shorter than a cold start


@pytest.fixture()
def slow_viva_api():
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, body: dict) -> None:
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # capabilities / health probes answer at once
            self._reply({})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            time.sleep(DELAY)
            self._reply({"job_name": "env-worker-test", "result": {"ok": True}})

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_the_default_timeout_really_is_too_short_for_the_server(slow_viva_api):
    """Control: an ordinary POST with the client's default timeout does fail here, so the tests below can fail."""
    client = SmsApiClient(slow_viva_api, timeout=CLIENT_DEFAULT)
    with pytest.raises(SmsApiError):
        client._post("/anything", json_body={})


def test_starting_a_relayed_worker_waits_for_the_accept_timeout_it_asked_for(slow_viva_api):
    client = SmsApiClient(slow_viva_api, timeout=CLIENT_DEFAULT)
    handle = client.start_relayed_env_worker(commit="deadbeef", accept_timeout=10)
    assert handle["job_name"] == "env-worker-test"


def test_a_relayed_call_waits_for_the_per_call_timeout_it_sent(slow_viva_api):
    client = SmsApiClient(slow_viva_api, timeout=CLIENT_DEFAULT)
    out = client.call_relayed_env_worker("env-worker-test", method="composites_full", timeout=10)
    assert out["result"] == {"ok": True}

"""Hosting a launcher viewer under the workbench's own address (lib/viewer_proxy.py).

A launch result with ``"proxy": True`` and a loopback URL is rewritten to
``/api/analysis-viewer/<uid>/app/<key>/…``, and requests under that path are
forwarded to the viewer's own server. A real loopback HTTP server stands in for
the viewer here, so the forwarding is exercised over a socket.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import vivarium_workbench.api.app as _app
from vivarium_workbench.api.app import create_app, get_workspace
from vivarium_workbench.lib import viewer_proxy

UID = "pkg::viewer"
Q_UID = quote(UID, safe="")


class _Viewer(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN002 — quiet
        pass

    def _send(self, status, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Internal", "not forwarded")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            return self._send(200, "<html>page</html>", "text/html; charset=utf-8")
        if self.path.startswith("/missing"):
            return self._send(404, json.dumps({"error": "no such thing"}))
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if self.path == "/big":
            return self._send(200, b"x" * 300_000, "application/octet-stream")
        return self._send(200, json.dumps({"path": self.path, "accept": self.headers.get("accept")}))

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        return self._send(200, json.dumps({"path": self.path, "body": body.decode(),
                                           "ctype": self.headers.get("content-type")}))


@pytest.fixture
def viewer():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Viewer)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def _clear_hosted():
    viewer_proxy._HOSTED.clear()
    yield
    viewer_proxy._HOSTED.clear()


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: tmp_path
    return TestClient(app, base_url="http://127.0.0.1")


def _launch(client, monkeypatch, result, **params):
    monkeypatch.setattr(_app._analysis_viewers, "resolve_launch",
                        lambda ws, uid, **kw: dict(result))
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/analysis-viewer/{Q_UID}/launch" + (f"?{qs}" if qs else ""))


def _hosted_prefix(client, monkeypatch, viewer) -> str:
    url = _launch(client, monkeypatch, {"url": viewer + "/", "proxy": True}).json()["url"]
    assert url.startswith(f"/api/analysis-viewer/{Q_UID}/app/") and url.endswith("/")
    return url


# ---- the launch route -------------------------------------------------------

def test_launch_with_proxy_returns_a_workbench_address(client, monkeypatch, viewer):
    r = _launch(client, monkeypatch, {"url": viewer + "/sub/page?x=1#runs=&study=s", "proxy": True})
    assert r.status_code == 200
    body = r.json()
    assert "proxy" not in body
    assert body["url"].startswith(f"/api/analysis-viewer/{Q_UID}/app/")
    assert body["url"].endswith("/sub/page?x=1#runs=&study=s")


def test_launch_gives_one_address_per_workspace_and_viewer(client, monkeypatch, viewer):
    a = _launch(client, monkeypatch, {"url": viewer + "/#a", "proxy": True}).json()["url"]
    b = _launch(client, monkeypatch, {"url": viewer + "/#b", "proxy": True}).json()["url"]
    assert a.split("#")[0] == b.split("#")[0]


def test_launch_without_proxy_is_unchanged(client, monkeypatch, viewer):
    r = _launch(client, monkeypatch, {"url": viewer + "/#x"})
    assert r.json() == {"url": viewer + "/#x"}


@pytest.mark.parametrize("url", ["https://example.org/viewer", "http://10.0.0.5:8000/",
                                 "http://127.0.0.1/no-port", "file:///etc/passwd"])
def test_launch_only_hosts_a_loopback_port(client, monkeypatch, url):
    r = _launch(client, monkeypatch, {"url": url, "proxy": True})
    assert r.json() == {"url": url}
    assert viewer_proxy._HOSTED == {}


def test_launch_error_is_unchanged(client, monkeypatch):
    r = _launch(client, monkeypatch, {"error": "no cache", "status": 404, "proxy": True})
    assert r.status_code == 404
    assert r.json() == {"error": "no cache"}


# ---- forwarding -------------------------------------------------------------

def test_page_and_api_are_forwarded(client, monkeypatch, viewer):
    p = _hosted_prefix(client, monkeypatch, viewer)
    page = client.get(p)
    assert page.status_code == 200
    assert page.text == "<html>page</html>"
    assert page.headers["content-type"].startswith("text/html")
    assert "x-internal" not in page.headers
    r = client.get(p + "api/meta?run=a%3Ab&x=1", headers={"accept": "application/json"})
    assert r.json() == {"path": "/api/meta?run=a%3Ab&x=1", "accept": "application/json"}


def test_post_body_is_forwarded(client, monkeypatch, viewer):
    p = _hosted_prefix(client, monkeypatch, viewer)
    r = client.post(p + "api/replay", json={"generation": 3})
    assert r.json() == {"path": "/api/replay", "body": '{"generation":3}', "ctype": "application/json"}


def test_viewer_errors_and_redirects_pass_through(client, monkeypatch, viewer):
    p = _hosted_prefix(client, monkeypatch, viewer)
    r = client.get(p + "missing")
    assert r.status_code == 404 and r.json() == {"error": "no such thing"}
    r = client.get(p + "moved", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/elsewhere"


def test_large_response_arrives_whole(client, monkeypatch, viewer):
    p = _hosted_prefix(client, monkeypatch, viewer)
    assert len(client.get(p + "big").content) == 300_000


def test_root_without_slash_redirects(client, monkeypatch, viewer):
    p = _hosted_prefix(client, monkeypatch, viewer)
    r = client.get(p.rstrip("/") + "?run=x", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == p + "?run=x"


def test_unknown_address_says_to_open_again(client):
    r = client.get(f"/api/analysis-viewer/{Q_UID}/app/0123456789abcdef/")
    assert r.status_code == 404
    assert "Open this viewer again" in r.text


def test_address_is_bound_to_its_viewer(client, monkeypatch, viewer):
    p = _hosted_prefix(client, monkeypatch, viewer)
    other = p.replace(Q_UID, quote("pkg::other", safe=""))
    assert client.get(other).status_code == 404


def test_stopped_viewer_is_launched_again(client, monkeypatch, viewer):
    p = _hosted_prefix(client, monkeypatch, viewer)
    key = p.rstrip("/").rsplit("/", 1)[-1]
    h = viewer_proxy._HOSTED[key]
    viewer_proxy._HOSTED[key] = h._replace(origin="http://127.0.0.1:9")     # nothing listens there
    calls = []
    monkeypatch.setattr(_app._analysis_viewers, "resolve_launch",
                        lambda ws, uid, **kw: calls.append(uid) or {"url": viewer + "/"})
    assert client.get(p + "api/meta").json()["path"] == "/api/meta"
    assert calls == [UID]
    assert viewer_proxy._HOSTED[key].origin == viewer


def test_viewer_that_cannot_start_answers_502(client, monkeypatch, viewer):
    p = _hosted_prefix(client, monkeypatch, viewer)
    key = p.rstrip("/").rsplit("/", 1)[-1]
    viewer_proxy._HOSTED[key] = viewer_proxy._HOSTED[key]._replace(origin="http://127.0.0.1:9")
    monkeypatch.setattr(_app._analysis_viewers, "resolve_launch",
                        lambda ws, uid, **kw: {"error": "no cache", "status": 404})
    r = client.get(p + "api/meta")
    assert r.status_code == 502
    assert "starting it again failed" in r.text


def test_forwarding_routes_stay_out_of_the_api_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not any("/app/" in p for p in paths)

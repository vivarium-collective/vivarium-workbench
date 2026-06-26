"""Tests for the same-origin (CSRF/Origin) guard on mutating requests.

The dashboard is a stdlib ``http.server`` bound to loopback that accepts
state-mutating POST/DELETE requests which can run git/gh/pip/shell. A
malicious web page could issue cross-origin "simple" requests to loopback
to trigger those endpoints. ``Handler._csrf_ok`` enforces a conservative
same-origin allowlist:

  * Origin ABSENT  -> ALLOW (curl / local CLI / same-origin navigations).
  * Origin PRESENT -> must match the request Host (same-origin) else 403.

The same-origin SPA served by this same server therefore keeps working
(its fetches send Origin == Host, or no Origin) while cross-site forged
requests are rejected with 403.

The shared ``dashboard_client`` fixture's ``_Client`` can't set arbitrary
headers, so these tests issue raw ``urllib`` requests against the spawned
server and only assert on the status code. The probe path is a bogus
``/api/*`` route: if the guard ALLOWS the request it reaches dispatch and
404s (side-effect free); if the guard BLOCKS it, it 403s before dispatch.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

import pytest


_PROBE_PATH = "/api/__csrf_probe_nonexistent__"


def _minimal_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: csrf-test-ws\n")
    (ws / ".pbg").mkdir()
    return ws


def _post(base_url: str, path: str, *, origin: str | None) -> int:
    """POST an empty JSON body to ``path``; return the HTTP status code.

    urllib sets the ``Host`` header automatically from the URL netloc, so a
    same-origin request just needs ``Origin`` set to that same scheme://netloc.
    """
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    req = urllib.request.Request(
        base_url + path, data=b"{}", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_post_cross_origin_is_rejected_403(tmp_path, dashboard_client):
    """A POST whose Origin differs from Host is forbidden (403)."""
    ws = _minimal_workspace(tmp_path)
    client = dashboard_client(ws)
    code = _post(client.base_url, _PROBE_PATH, origin="http://evil.example.com")
    assert code == 403


def test_post_same_origin_is_allowed(tmp_path, dashboard_client):
    """A POST whose Origin matches Host reaches dispatch (404, not 403).

    This is the SPA path: same-origin fetches send Origin == Host.
    """
    ws = _minimal_workspace(tmp_path)
    client = dashboard_client(ws)
    # client.base_url is "http://127.0.0.1:<port>" == scheme://Host.
    code = _post(client.base_url, _PROBE_PATH, origin=client.base_url)
    assert code != 403
    assert code == 404  # passed the guard, fell through to the route map


def test_post_no_origin_is_allowed(tmp_path, dashboard_client):
    """A POST with no Origin header reaches dispatch (curl / CLI path)."""
    ws = _minimal_workspace(tmp_path)
    client = dashboard_client(ws)
    code = _post(client.base_url, _PROBE_PATH, origin=None)
    assert code != 403
    assert code == 404


# ---------------------------------------------------------------------------
# Parity: the real ``Handler._csrf_ok`` must return the SAME verdicts as the
# pure ``lib.csrf.is_request_allowed`` across the cases (single-sourcing check),
# and emit the exact 403 JSON on deny.
# ---------------------------------------------------------------------------

_CSRF_CASES = [
    # (origin, host) — None origin means header absent.
    (None, "127.0.0.1:8080"),
    ("http://127.0.0.1:8080", "127.0.0.1:8080"),   # same-origin
    ("https://app.example.com", "app.example.com"),
    ("http://evil.example.com", "127.0.0.1:8080"), # cross-origin
    ("not-a-url", "127.0.0.1:8080"),               # empty netloc
]


class _FakeHeaders(dict):
    """Case-insensitive-ish header map; _csrf_ok reads 'Origin' and 'Host'."""


def _make_handler(origin, host):
    from vivarium_dashboard import server

    h = object.__new__(server.Handler)
    hdrs = {}
    if origin is not None:
        hdrs["Origin"] = origin
    hdrs["Host"] = host
    h.headers = hdrs  # http.server's headers support .get()
    captured = {}

    def _json(obj, code):
        captured.update(obj=obj, code=code)

    h._json = _json  # type: ignore[attr-defined]
    return h, captured


@pytest.mark.parametrize("origin, host", _CSRF_CASES)
def test_csrf_ok_matches_pure_predicate(origin, host, monkeypatch):
    from vivarium_dashboard.lib import csrf

    monkeypatch.delenv("VIVARIUM_DASHBOARD_DISABLE_CSRF", raising=False)
    expected = csrf.is_request_allowed(origin, host, disabled=False)

    h, captured = _make_handler(origin, host)
    verdict = h._csrf_ok()
    assert verdict is expected
    if not expected:
        # On deny the live path emits the exact 403 JSON and returns False.
        assert captured["code"] == 403
        assert captured["obj"] == {"error": "cross-origin request forbidden"}


def test_csrf_ok_env_disable_bypasses(monkeypatch):
    monkeypatch.setenv("VIVARIUM_DASHBOARD_DISABLE_CSRF", "1")
    # Cross-origin would normally deny; env bypass allows.
    h, captured = _make_handler("http://evil.example.com", "127.0.0.1:8080")
    assert h._csrf_ok() is True
    assert captured == {}

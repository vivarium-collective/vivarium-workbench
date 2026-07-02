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
    # Passed the guard and reached routing: 404 (no route) or 405 (the FastAPI
    # static catch-all matches the path for GET, so POST is method-not-allowed).
    assert code in (404, 405)


def test_post_no_origin_is_allowed(tmp_path, dashboard_client):
    """A POST with no Origin header reaches dispatch (curl / CLI path)."""
    ws = _minimal_workspace(tmp_path)
    client = dashboard_client(ws)
    code = _post(client.base_url, _PROBE_PATH, origin=None)
    assert code != 403
    # Passed the guard and reached routing (404 no-route or 405 method-not-allowed
    # from the FastAPI static catch-all — see test_post_same_origin_is_allowed).
    assert code in (404, 405)


# ---------------------------------------------------------------------------
# Pure predicate: ``lib.csrf.is_request_allowed`` is the single source the
# FastAPI CSRF middleware shares (see api/app.py ``_csrf_mw``).  These lock its
# verdicts across the case matrix (the behavioral 200-vs-403 wiring through the
# live app is covered by the dashboard_client tests above and in
# tests/test_api_app.py::test_cross_origin_post_403).
# ---------------------------------------------------------------------------

_CSRF_CASES = [
    # (origin, host, expected_allowed) — None origin means header absent.
    (None, "127.0.0.1:8080", True),                       # no Origin → allow
    ("http://127.0.0.1:8080", "127.0.0.1:8080", True),    # same-origin
    ("https://app.example.com", "app.example.com", True), # same-origin (cross-scheme host match)
    ("http://evil.example.com", "127.0.0.1:8080", False), # cross-origin
    ("not-a-url", "127.0.0.1:8080", False),               # empty netloc → deny
]


@pytest.mark.parametrize("origin, host, expected", _CSRF_CASES)
def test_csrf_predicate_verdicts(origin, host, expected):
    from vivarium_workbench.lib import csrf

    assert csrf.is_request_allowed(origin, host, disabled=False) is expected


def test_csrf_predicate_env_disable_bypasses():
    from vivarium_workbench.lib import csrf

    # Cross-origin would normally deny; the disabled bypass allows.
    assert csrf.is_request_allowed(
        "http://evil.example.com", "127.0.0.1:8080", disabled=True) is True
    # And the env reader recognises the escape hatch.
    assert csrf.is_disabled_via_env({"VIVARIUM_DASHBOARD_DISABLE_CSRF": "1"}) is True
    assert csrf.is_disabled_via_env({}) is False

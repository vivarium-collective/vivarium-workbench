"""Live-server check for the session-cookie seam (slice 1).

A real browser gets a `vw_session` cookie minted on first response; cookie-less
clients (like this urllib harness) keep working unchanged (behavior-preserving).
"""
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "_fixtures"
_WS = _FIXTURES / "ws_increase_demo"


@pytest.mark.skipif(not _WS.is_dir(), reason="fixture workspace not present")
def test_session_cookie_minted_and_behavior_preserved(dashboard_client):
    client = dashboard_client(_WS)

    # Behavior-preserving: the endpoint still answers exactly as before.
    r = client.get("/health")
    assert r.status_code == 200

    # A fresh (cookie-less) request gets a session cookie minted on the response,
    # HttpOnly + SameSite=Lax, so a real browser carries a session going forward.
    set_cookie = r.headers.get("set-cookie", "")
    assert "vw_session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()

"""Parity tests for the pure GitHub-auth builders (``lib.auth_views``).

Each builder is a thin wrapper over a ``lib.github_auth`` function whose
device-flow/session state lives in that module's process-global singletons.
We monkeypatch the ``github_auth`` functions (reached via ``auth_views.github_auth``)
with canned returns and assert the exact ``(dict, status_code)`` mapping — so no
test ever touches real GitHub.
"""

from __future__ import annotations

import pytest

from vivarium_dashboard.lib import auth_views


# ---------------------------------------------------------------------------
# auth_start — 503 (no_client_id) / 502 (other error) / 200 (success)
# ---------------------------------------------------------------------------


def test_auth_start_no_client_id_503(monkeypatch):
    monkeypatch.setattr(
        auth_views.github_auth, "start_device_flow",
        lambda: {"error": "no_client_id", "hint": "set env"},
    )
    body, code = auth_views.auth_start({})
    assert code == 503
    assert body == {"error": "no_client_id", "hint": "set env"}


def test_auth_start_other_error_502(monkeypatch):
    monkeypatch.setattr(
        auth_views.github_auth, "start_device_flow",
        lambda: {"error": "device_code_failed", "status": 500},
    )
    body, code = auth_views.auth_start({})
    assert code == 502
    assert body["error"] == "device_code_failed"


def test_auth_start_success_200(monkeypatch):
    payload = {
        "flow_id": "abc", "user_code": "WXYZ-1234",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900, "interval": 5,
    }
    monkeypatch.setattr(auth_views.github_auth, "start_device_flow", lambda: payload)
    body, code = auth_views.auth_start(None)
    assert code == 200
    assert body == payload


# ---------------------------------------------------------------------------
# auth_poll — missing flow_id 400 + status→code map
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flow_id", ["", "   ", None])
def test_auth_poll_missing_flow_id_400(flow_id, monkeypatch):
    # The github_auth.poll fn must NOT be called when flow_id is blank.
    called = {"n": 0}

    def _poll(_fid):
        called["n"] += 1
        return {"status": "ok"}

    monkeypatch.setattr(auth_views.github_auth, "poll_device_flow", _poll)
    body, code = auth_views.auth_poll(flow_id)  # type: ignore[arg-type]
    assert code == 400
    assert body == {"status": "error", "detail": "missing_flow_id"}
    assert called["n"] == 0


@pytest.mark.parametrize(
    "status,expected_code",
    [
        ("ok", 200),
        ("pending", 202),
        ("expired", 410),
        ("denied", 403),
        ("error", 400),
        ("something_unknown", 400),
    ],
)
def test_auth_poll_status_to_code(status, expected_code, monkeypatch):
    result = {"status": status, "detail": "x"}
    monkeypatch.setattr(
        auth_views.github_auth, "poll_device_flow", lambda fid: result,
    )
    body, code = auth_views.auth_poll("flow-123")
    assert code == expected_code
    assert body == result


def test_auth_poll_strips_whitespace(monkeypatch):
    seen = {}

    def _poll(fid):
        seen["fid"] = fid
        return {"status": "pending"}

    monkeypatch.setattr(auth_views.github_auth, "poll_device_flow", _poll)
    body, code = auth_views.auth_poll("  flow-9  ")
    assert seen["fid"] == "flow-9"
    assert code == 202


# ---------------------------------------------------------------------------
# auth_status — always 200
# ---------------------------------------------------------------------------


def test_auth_status_unauthenticated_200(monkeypatch):
    monkeypatch.setattr(
        auth_views.github_auth, "status_payload", lambda: {"authenticated": False},
    )
    body, code = auth_views.auth_status()
    assert code == 200
    assert body == {"authenticated": False}


def test_auth_status_authenticated_200(monkeypatch):
    payload = {
        "authenticated": True, "login": "octocat",
        "source": "device_flow", "scopes": ["repo"],
    }
    monkeypatch.setattr(auth_views.github_auth, "status_payload", lambda: payload)
    body, code = auth_views.auth_status()
    assert code == 200
    assert body == payload


# ---------------------------------------------------------------------------
# auth_logout — ({"ok": True}, 200) AND logout() actually called
# ---------------------------------------------------------------------------


def test_auth_logout_calls_logout_and_returns_ok(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(
        auth_views.github_auth, "logout",
        lambda: called.__setitem__("n", called["n"] + 1),
    )
    body, code = auth_views.auth_logout({"anything": "ignored"})
    assert code == 200
    assert body == {"ok": True}
    assert called["n"] == 1


# ---------------------------------------------------------------------------
# auth_orgs — 401 (unauthenticated) / 502 (other error) / 200 (success)
# ---------------------------------------------------------------------------


def test_auth_orgs_unauthenticated_401(monkeypatch):
    monkeypatch.setattr(
        auth_views.github_auth, "list_orgs", lambda: {"error": "unauthenticated"},
    )
    body, code = auth_views.auth_orgs()
    assert code == 401
    assert body == {"error": "unauthenticated"}


def test_auth_orgs_other_error_502(monkeypatch):
    monkeypatch.setattr(
        auth_views.github_auth, "list_orgs",
        lambda: {"error": "orgs_lookup_failed", "status": 500},
    )
    body, code = auth_views.auth_orgs()
    assert code == 502
    assert body["error"] == "orgs_lookup_failed"


def test_auth_orgs_success_200(monkeypatch):
    payload = {
        "login": "octocat",
        "orgs": [
            {"name": "octocat", "kind": "personal"},
            {"name": "vivarium-collective", "kind": "org"},
        ],
    }
    monkeypatch.setattr(auth_views.github_auth, "list_orgs", lambda: payload)
    body, code = auth_views.auth_orgs()
    assert code == 200
    assert body == payload

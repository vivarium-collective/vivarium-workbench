"""Unit tests for ``vivarium_workbench.lib.github_auth`` (todo #8 Phase B-bis).

Tests run entirely in-process and mock the GitHub HTTP endpoints + the ``gh``
CLI delegate. No real network calls; no real keyring writes (keyring is
isolated via ``monkeypatch`` of the module-level lookup helpers).
"""
from __future__ import annotations

import pytest

from vivarium_workbench.lib import github_auth as ga


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch, tmp_path):
    """Reset cached session + pending flows + fake the keyring + last-login file."""
    ga._set_cached_session(None)
    with ga._STATE_LOCK:
        ga._PENDING_FLOWS.clear()

    fake_keyring: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(ga, "_keyring_available", lambda: True)
    monkeypatch.setattr(ga, "_keyring_get",
                        lambda login: fake_keyring.get((ga._KEYRING_SERVICE, login)))
    def _set(login, token):
        fake_keyring[(ga._KEYRING_SERVICE, login)] = token
        return True
    def _del(login):
        fake_keyring.pop((ga._KEYRING_SERVICE, login), None)
    monkeypatch.setattr(ga, "_keyring_set", _set)
    monkeypatch.setattr(ga, "_keyring_delete", _del)

    # Redirect the last-login hint file under tmp_path so tests don't pollute
    # the real ~/.config.
    last_login_path = tmp_path / "last_login"
    monkeypatch.setattr(ga, "_last_login_path", lambda: last_login_path)

    # Default: pretend gh is not installed. Individual tests override.
    monkeypatch.setattr(ga, "_gh_available", lambda: False)
    monkeypatch.setattr(ga, "_gh_auth_ok", lambda: False)

    yield


# ---------------------------------------------------------------------------
# mask_token
# ---------------------------------------------------------------------------


def test_mask_token_redacts_classic_pat():
    s = "the token is ghp_abcdefghijklmnopqrstuvwxyz0123456789AB and that's it"
    out = ga.mask_token(s)
    assert "ghp_" not in out
    assert "<redacted>" in out


def test_mask_token_covers_all_prefixes():
    for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"):
        s = f"err: {prefix}aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa boom"
        assert prefix not in ga.mask_token(s)


def test_mask_token_is_idempotent():
    s = "before <redacted> ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa after"
    once = ga.mask_token(s)
    twice = ga.mask_token(once)
    assert once == twice


def test_mask_token_leaves_unrelated_text_intact():
    assert ga.mask_token("nothing to hide here") == "nothing to hide here"


# ---------------------------------------------------------------------------
# Device flow
# ---------------------------------------------------------------------------


def _post_returns(status: int, payload: dict):
    return lambda url, data, headers=None: (status, payload)


def _get_returns(status: int, payload: dict):
    return lambda url, token=None: (status, payload)


def test_start_device_flow_no_client_id_returns_error(monkeypatch):
    monkeypatch.delenv(ga._CLIENT_ID_ENV, raising=False)
    out = ga.start_device_flow()
    assert out["error"] == "no_client_id"


def test_start_device_flow_success_stores_pending(monkeypatch):
    monkeypatch.setenv(ga._CLIENT_ID_ENV, "Iv1.testclient")
    monkeypatch.setattr(ga, "_http_post", _post_returns(200, {
        "device_code": "DEV-CODE-xyz",
        "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 5,
    }))
    result = ga.start_device_flow()
    assert "flow_id" in result
    assert result["user_code"] == "ABCD-1234"
    assert "device_code" not in result, "device_code must never leave the server"
    with ga._STATE_LOCK:
        assert result["flow_id"] in ga._PENDING_FLOWS
        assert ga._PENDING_FLOWS[result["flow_id"]]["device_code"] == "DEV-CODE-xyz"


def test_poll_pending_then_ok_persists_session(monkeypatch):
    monkeypatch.setenv(ga._CLIENT_ID_ENV, "Iv1.testclient")
    monkeypatch.setattr(ga, "_http_post", _post_returns(200, {
        "device_code": "DEV", "user_code": "AAAA-BBBB",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900, "interval": 5,
    }))
    flow = ga.start_device_flow()

    # First poll: pending.
    monkeypatch.setattr(ga, "_http_post",
                        _post_returns(200, {"error": "authorization_pending"}))
    r1 = ga.poll_device_flow(flow["flow_id"])
    assert r1["status"] == "pending"

    # Second poll: success. /user lookup returns the login.
    monkeypatch.setattr(ga, "_http_post",
                        _post_returns(200, {"access_token": "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}))
    monkeypatch.setattr(ga, "_http_get", _get_returns(200, {"login": "octocat"}))
    r2 = ga.poll_device_flow(flow["flow_id"])
    assert r2 == {"status": "ok", "login": "octocat"}

    # Session is cached and persisted; status_payload reflects it.
    payload = ga.status_payload()
    assert payload["authenticated"] is True
    assert payload["login"] == "octocat"
    assert payload["source"] == "device_flow"

    # current_token_env injects the token under both names.
    env = ga.current_token_env()
    assert env["GH_TOKEN"] == "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert env["GITHUB_TOKEN"] == env["GH_TOKEN"]
    assert env["GH_USER"] == "octocat"

    # Pending flow entry was cleaned up.
    with ga._STATE_LOCK:
        assert flow["flow_id"] not in ga._PENDING_FLOWS


def test_poll_expired_returns_expired(monkeypatch):
    monkeypatch.setenv(ga._CLIENT_ID_ENV, "Iv1.testclient")
    monkeypatch.setattr(ga, "_http_post", _post_returns(200, {
        "device_code": "DEV", "user_code": "X", "verification_uri": "u",
        "expires_in": 900, "interval": 5,
    }))
    flow = ga.start_device_flow()
    monkeypatch.setattr(ga, "_http_post",
                        _post_returns(200, {"error": "expired_token"}))
    out = ga.poll_device_flow(flow["flow_id"])
    assert out == {"status": "expired"}
    assert ga.status_payload() == {"authenticated": False}


def test_poll_denied_returns_denied(monkeypatch):
    monkeypatch.setenv(ga._CLIENT_ID_ENV, "Iv1.testclient")
    monkeypatch.setattr(ga, "_http_post", _post_returns(200, {
        "device_code": "DEV", "user_code": "X", "verification_uri": "u",
        "expires_in": 900, "interval": 5,
    }))
    flow = ga.start_device_flow()
    monkeypatch.setattr(ga, "_http_post",
                        _post_returns(200, {"error": "access_denied"}))
    out = ga.poll_device_flow(flow["flow_id"])
    assert out == {"status": "denied"}


def test_poll_unknown_flow_returns_error():
    out = ga.poll_device_flow("not-a-real-flow-id")
    assert out["status"] == "error"
    assert out["detail"] == "no_client_id" or out["detail"] == "unknown_flow"


def test_poll_slow_down_bumps_interval(monkeypatch):
    monkeypatch.setenv(ga._CLIENT_ID_ENV, "Iv1.testclient")
    monkeypatch.setattr(ga, "_http_post", _post_returns(200, {
        "device_code": "DEV", "user_code": "X", "verification_uri": "u",
        "expires_in": 900, "interval": 5,
    }))
    flow = ga.start_device_flow()
    monkeypatch.setattr(ga, "_http_post",
                        _post_returns(200, {"error": "slow_down", "interval": 10}))
    out = ga.poll_device_flow(flow["flow_id"])
    assert out["status"] == "pending"
    assert out["interval"] == 10
    with ga._STATE_LOCK:
        assert ga._PENDING_FLOWS[flow["flow_id"]]["interval"] == 10


# ---------------------------------------------------------------------------
# gh-cli delegate
# ---------------------------------------------------------------------------


def test_gh_cli_session_wins_when_available(monkeypatch):
    """If gh is installed and authed, current_session reports source=gh_cli."""
    monkeypatch.setattr(ga, "_gh_available", lambda: True)
    monkeypatch.setattr(ga, "_gh_auth_ok", lambda: True)
    monkeypatch.setattr(ga, "_gh_token", lambda: "ghp_cli_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    monkeypatch.setattr(ga, "_gh_login", lambda: "alex")

    session = ga.current_session()
    assert session is not None
    assert session.login == "alex"
    assert session.source == "gh_cli"
    assert ga.status_payload()["source"] == "gh_cli"


def test_no_session_when_nothing_configured(monkeypatch):
    """No gh, no keyring entry, no env var → unauthenticated."""
    monkeypatch.delenv(ga._CLIENT_ID_ENV, raising=False)
    assert ga.current_session() is None
    assert ga.status_payload() == {"authenticated": False}
    assert ga.current_token_env() == {}


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_clears_cache_and_keyring(monkeypatch):
    """After logout, status reverts to unauthenticated and keyring entry is gone."""
    monkeypatch.setenv(ga._CLIENT_ID_ENV, "Iv1.testclient")
    monkeypatch.setattr(ga, "_http_post", _post_returns(200, {
        "device_code": "DEV", "user_code": "X", "verification_uri": "u",
        "expires_in": 900, "interval": 5,
    }))
    flow = ga.start_device_flow()
    monkeypatch.setattr(ga, "_http_post",
                        _post_returns(200, {"access_token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}))
    monkeypatch.setattr(ga, "_http_get", _get_returns(200, {"login": "octocat"}))
    ga.poll_device_flow(flow["flow_id"])

    assert ga.status_payload()["authenticated"] is True
    assert ga._keyring_get("octocat") == "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

    ga.logout()

    assert ga.status_payload() == {"authenticated": False}
    assert ga._keyring_get("octocat") is None

"""Tests that ``current_token_env()`` secrets are properly injected into
subprocess environments and never leak to disk or logs.

(Phase G of todo #8.)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from vivarium_dashboard.lib import github_auth as ga


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Same isolation pattern as test_github_auth.py."""
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
    last_login_path = tmp_path / "last_login"
    monkeypatch.setattr(ga, "_last_login_path", lambda: last_login_path)
    monkeypatch.setattr(ga, "_gh_available", lambda: False)
    monkeypatch.setattr(ga, "_gh_auth_ok", lambda: False)

    yield


def _populate_session(token: str = "ghp_test_token_aaaaaaaaaaaaaaaaaaaaaaaaaaaa"):
    """Bootstrap a device-flow session so current_token_env() works."""
    ga._set_cached_session(ga.Session(
        login="testuser", token=token, scopes=["repo", "read:org"],
        source="device_flow",
    ))


def test_current_token_env_returns_token_vars():
    _populate_session()
    env = ga.current_token_env()
    assert env["GH_TOKEN"] == "ghp_test_token_aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert env["GITHUB_TOKEN"] == env["GH_TOKEN"]
    assert env["GH_USER"] == "testuser"


def test_current_token_env_returns_empty_when_unauthenticated():
    assert ga.current_token_env() == {}


def test_subprocess_inherits_token(tmp_path):
    """Spawn a subprocess and verify GH_TOKEN is visible in the child's
    environment."""
    _populate_session("ghp_subprocess_token_test_aaaaaaaaaaaaaaaaaaaaaa")
    env = os.environ.copy()
    env.update(ga.current_token_env())

    script = (
        "import os; "
        "print(os.environ.get('GH_TOKEN', 'MISSING'), end=''); "
        "print(':' + os.environ.get('GH_USER', 'MISSING'), end='')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0
    assert "ghp_subprocess_token_test" in result.stdout
    assert "testuser" in result.stdout


def test_subprocess_without_token_env_not_leaked(tmp_path):
    """Without current_token_env() in the subprocess env, GH_TOKEN is absent
    (verifying the code doesn't accidentally inject it all the time)."""
    _populate_session("ghp_should_not_leak")
    env = os.environ.copy()
    # Deliberately NOT calling current_token_env().

    script = "import os; print(os.environ.get('GH_TOKEN', 'MISSING'), end='')"
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == "MISSING"


def test_mask_token_applied_to_stderr_capture():
    """Verifies the mask_token helper redacts any leaked token from captured
    stderr/stdout output, such as what the workspace_create pipeline captures
    from gh subprocesses."""
    output = (
        "fatal: could not read Username for 'https://github.com': "
        "terminal prompts disabled\ntoken: "
        "ghp_should_be_redacted_aaaaaaaaaaaaaaaaaaaaaaaaaa\n"
    )
    masked = ga.mask_token(output)
    assert "ghp_" not in masked
    assert "<redacted>" in masked


def test_current_token_env_keyring_persisted(monkeypatch):
    """After device-flow auth, the token persists in keyring and
    current_token_env() can reconstruct the env vars on a fresh cache (cold
    start scenario)."""
    monkeypatch.setenv(ga._CLIENT_ID_ENV, "Iv1.testclient")

    # Mock _http_post to return the right payload for each endpoint:
    # device-code endpoint → device_code + user_code for start_device_flow
    # token endpoint → access_token for poll_device_flow
    def _mock_post(url, data, headers=None):
        if "device/code" in url:
            return (200, {
                "device_code": "dc_keyring_persist_aaaaaaaaaaaaaaaaaaaaaaaa",
                "user_code": "KEYR-ING",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "expires_in": 900,
            })
        return (200, {"access_token": "ghp_keyring_persist_aaaaaaaaaaaaaaaaaaaaaaaaa"})
    monkeypatch.setattr(ga, "_http_post", _mock_post)

    monkeypatch.setattr(ga, "_http_get", lambda url, token=None: (
        200, {"login": "keyring-user"}
    ))

    ga._set_cached_session(None)
    flow = ga.start_device_flow()
    assert "flow_id" in flow, f"start_device_flow failed: {flow}"
    ga.poll_device_flow(flow["flow_id"])

    # Wipe the in-memory cache to simulate cold start.
    ga._set_cached_session(None)

    env = ga.current_token_env()
    assert env["GH_TOKEN"] == "ghp_keyring_persist_aaaaaaaaaaaaaaaaaaaaaaaaa"
    assert env["GH_USER"] == "keyring-user"

"""S-14: the chat can be switched off, and is off by default where more than one person can reach the server.

Real here: the FastAPI app and its routes, the CLI entry and a real detached server. Nothing is stubbed.
"""
import json
import shutil
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("pydantic_ai")
from fastapi.testclient import TestClient  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_auth  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_CHAT", raising=False)
    ai_auth.configure_default(True)
    yield
    ai_auth.configure_default(True)


@pytest.fixture
def client(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: t\n")
    app = appmod.create_app()
    app.state.bind_host = "127.0.0.1"
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    return TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_the_env_switch_turns_the_chat_off_everywhere(client, monkeypatch, value):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT", value)
    body = client.get("/api/ai/status").json()
    assert body["available"] is False and "VIVARIUM_WORKBENCH_CHAT" in body["reason"]
    assert client.post("/api/chat/turn", json={"messages": [], "prompt": "hi"},
                       headers={"X-VW-Session": "t"}).status_code == 503
    assert client.post("/api/ai/credentials", json={"provider": "openai", "model": "m", "api_key": "k"},
                       headers={"X-VW-Session": "t"}).status_code == 503


def test_the_chat_is_on_by_default_where_the_bind_is_private(client):
    assert client.get("/api/ai/status").json()["available"] is True


def test_the_chat_is_off_by_default_when_the_bind_is_not_private(client):
    ai_auth.configure_default(False)
    body = client.get("/api/ai/status").json()
    assert body["available"] is False and "VIVARIUM_WORKBENCH_CHAT=1" in body["reason"]


def test_the_env_can_opt_a_shared_server_in(client, monkeypatch):
    ai_auth.configure_default(False)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT", "1")
    assert client.get("/api/ai/status").json()["available"] is True


@pytest.mark.parametrize("host, proxied, expected", [
    ("127.0.0.1", False, True), ("localhost", False, True), ("::1", False, True),
    ("0.0.0.0", False, False), ("10.0.0.5", False, False), ("127.0.0.1", True, False),
])
def test_the_default_follows_the_bind(host, proxied, expected):
    assert ai_auth.default_enabled_for_bind(host, proxied=proxied) is expected


def test_the_no_chat_flag_is_honoured_by_a_real_detached_server(tmp_path):
    """``serve --detach --no-chat`` -> the detached child -> ``/api/ai/status``."""
    from vivarium_workbench import cli

    def _args(**kw):
        ns = type("NS", (), {})()
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    pytest.importorskip("process_bigraph.artifacts")
    fixtures = Path(__file__).parent / "_fixtures"
    src = next((d for d in sorted(fixtures.iterdir()) if (d / "workspace.yaml").exists()), None)
    if src is None:
        pytest.skip("no fixture workspace available")
    ws = tmp_path / "ws_copy"
    shutil.copytree(src, ws)
    cli._clear_server_state(ws)
    try:
        rc = cli.cmd_serve(_args(
            workspace=str(ws), port=0, host="127.0.0.1", base_path="", detach=True, open=False,
            investigation=None, trust_proxy=False, allowed_origin=None, allowed_host=None, no_chat=True,
        ))
        assert rc == 0
        url = cli._read_server_info(ws)["url"]
        status = None
        for _ in range(100):
            try:
                status = httpx.get(url + "/api/ai/status", timeout=5).json()
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        assert status is not None and status["available"] is False, json.dumps(status)
    finally:
        cli.cmd_server_stop(_args(workspace=str(ws)))


def test_a_launch_that_never_configures_the_default_is_off():
    """Fail closed: e.g. ``uvicorn vivarium_workbench.api.app:app --host 0.0.0.0`` never runs ``serve``."""
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c", "from vivarium_workbench.lib import ai_auth; print(ai_auth.unavailable_reason())"],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "PYDANTIC_AI_NO_BANNER": "1",
                                             "HOME": "/nonexistent"}, timeout=120)
    assert "off by default" in out.stdout, (out.stdout, out.stderr)


def test_status_does_not_read_the_keyring_while_the_chat_is_off(client, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT", "0")

    def boom(*a, **k):
        raise AssertionError("the keyring was read for a switched-off chat")
    monkeypatch.setattr(ai_auth, "get_credential", boom)
    monkeypatch.setattr(ai_auth, "get_selection", boom)
    body = client.get("/api/ai/status").json()
    assert body["available"] is False and all(p["configured"] is False for p in body["providers"])

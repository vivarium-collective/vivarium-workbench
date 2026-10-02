"""Hosted-readiness limits on the AI endpoints: bounded request fields and a bounded key check.

Real HTTP throughout: the key check is aimed at a real local socket that accepts a connection and never answers, so
the proposition under test is "the check gives up", not "a mock returns quickly".
"""
import asyncio
import socket
import threading
import time

import keyring
import keyring.backends.fail
import pytest
from fastapi.testclient import TestClient

from vivarium_workbench.api import app as appmod
from vivarium_workbench.lib import ai_auth
from vivarium_workbench.lib.errors import APIError


@pytest.fixture(autouse=True)
def _no_real_keychain(tmp_path, monkeypatch):
    """Nothing here may reach the machine's real Keychain or config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    prev = keyring.get_keyring()
    keyring.set_keyring(keyring.backends.fail.Keyring())
    yield
    keyring.set_keyring(prev)


class _BlackHole:
    """Accepts TCP connections and never reads or writes."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.held = []
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        try:
            while True:
                conn, _ = self.sock.accept()
                self.held.append(conn)
        except OSError:
            pass

    def close(self):
        self.sock.close()
        for c in self.held:
            c.close()


@pytest.fixture
def black_hole():
    h = _BlackHole()
    yield h
    h.close()


def test_a_key_check_that_never_gets_an_answer_gives_up_in_bounded_time(black_hole, monkeypatch):
    monkeypatch.setattr(ai_auth, "CHECK_KEY_TIMEOUT", 1.5)
    cred = ai_auth.Credential(api_key=None, base_url=f"http://127.0.0.1:{black_hole.port}/v1")
    t0 = time.monotonic()
    with pytest.raises(APIError) as e:
        asyncio.run(ai_auth.check_key("ollama", "m", cred))
    assert time.monotonic() - t0 < 10, "the check must not wait on a server that never answers"
    assert e.value.status_code == 504 and "did not answer" in str(e.value)


@pytest.mark.parametrize("field,value", [
    ("api_key", "k" * 20_000),
    ("base_url", "http://127.0.0.1:1/" + "a" * 5_000),
    ("model", "m" * 1_000),
    ("provider", "p" * 200),
], ids=["api_key", "base_url", "model", "provider"])
def test_oversized_credential_fields_are_refused_before_any_work(field, value):
    body = {"provider": "openai", "model": "gpt-x", "api_key": "k"}
    body[field] = value
    c = TestClient(appmod.create_app(), base_url="http://127.0.0.1:8000")
    assert c.post("/api/ai/credentials", json=body).status_code == 422


def test_oversized_select_and_ollama_fields_are_refused():
    c = TestClient(appmod.create_app(), base_url="http://127.0.0.1:8000")
    assert c.post("/api/ai/select", json={"provider": "openai", "model": "m" * 1_000}).status_code == 422
    assert c.post("/api/ai/ollama-models", json={"base_url": "http://127.0.0.1:1/" + "a" * 5_000}).status_code == 422

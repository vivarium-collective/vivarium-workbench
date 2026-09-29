"""Provider credentials for the built-in chat (lib/ai_auth.py, /api/ai/*).

What is real here: the keyring API (a real ``KeyringBackend`` subclass — the OS
store is the periphery), pydantic-ai, the OpenAI SDK, the FastAPI app and its
middleware, the filesystem. What is stubbed: only the *remote LLM* — a local HTTP
server that answers ``/v1/chat/completions`` (401 for a wrong key, a completion
for the right one). Anthropic / Google / Bedrock wiring is NOT exercised here —
that needs a real provider (see the live probe in the PR).
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("pydantic_ai")
keyring = pytest.importorskip("keyring")
from fastapi.testclient import TestClient  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402
from keyring.backends import fail as keyring_fail  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_auth  # noqa: E402
from vivarium_workbench.lib.errors import APIError  # noqa: E402

GOOD_KEY = "sk-test-SECRETVALUE-1234567890abcdef"


class _MemKeyring(KeyringBackend):
    """A real keyring backend (same interface the OS backends implement)."""
    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict = {}
        self.touched = 0

    def get_password(self, service, username):
        self.touched += 1
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.touched += 1
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.touched += 1
        self.store.pop((service, username), None)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for env in ai_auth.ENV_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    ai_auth._MEMORY.clear()
    ai_auth._SELECTION.clear()
    prev = keyring.get_keyring()
    backend = _MemKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(prev)
    ai_auth._MEMORY.clear()
    ai_auth._SELECTION.clear()


@pytest.fixture
def stub_llm():
    """A local OpenAI-compatible endpoint: right key → completion, else 401 that
    echoes the presented key (so masking is observable)."""
    seen = {"auth": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            auth = self.headers.get("Authorization", "")
            seen["auth"].append(auth)
            if auth != f"Bearer {GOOD_KEY}":
                body = {"error": {"message": f"Incorrect API key provided: {auth[7:]}",
                                  "type": "invalid_request_error", "code": "invalid_api_key"}}
                code = 401
            else:
                body = {"id": "c1", "object": "chat.completion", "created": 0, "model": "m",
                        "choices": [{"index": 0, "finish_reason": "length",
                                     "message": {"role": "assistant", "content": "p"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                code = 200
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1", seen
    srv.shutdown()


def _client(bind_host="127.0.0.1"):
    app = appmod.create_app()
    app.state.bind_host = bind_host
    return TestClient(app)


def _save(c, base_url, key, **kw):
    return c.post("/api/ai/credentials", json={
        "provider": "openai-compatible", "model": "m", "base_url": base_url,
        "api_key": key, **kw})


# --- local bind: keyring ----------------------------------------------------


def test_local_save_checks_key_then_stores_in_keyring(stub_llm, _isolate):
    base, seen = stub_llm
    c = _client()
    r = _save(c, base, GOOD_KEY)
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "keyring"
    assert seen["auth"] == [f"Bearer {GOOD_KEY}"]          # exactly one real check request
    assert _isolate.store[(ai_auth.KEYRING_SERVICE, "openai-compatible")]
    st = c.get("/api/ai/status").json()
    assert st["storage_mode"] == "keyring" and st["available"] is True
    assert st["selected"] == {"provider": "openai-compatible", "model": "m"}
    row = next(p for p in st["providers"] if p["id"] == "openai-compatible")
    assert row == {"id": "openai-compatible", "configured": True, "source": "keyring",
                   "base_url": base}
    # selection persisted (non-secret) under the config dir, never the key
    text = ai_auth.selection_path().read_text()
    assert "openai-compatible" in text and GOOD_KEY not in text


def test_bad_key_is_401_masked_and_not_stored(stub_llm, _isolate):
    base, _ = stub_llm
    c = _client()
    bad = "sk-test-WRONGWRONGWRONGWRONG12345"
    r = _save(c, base, bad)
    assert r.status_code == 401
    assert bad not in r.text and "<redacted>" in r.text
    assert not _isolate.store
    assert c.get("/api/ai/status").json()["selected"] is None


def test_no_route_echoes_a_key(stub_llm, monkeypatch):
    base, _ = stub_llm
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-ENVSECRET-abcdefghijklmnop")
    c = _client()
    bodies = [_save(c, base, GOOD_KEY).text, c.get("/api/ai/status").text,
              c.post("/api/ai/select", json={"provider": "openai", "model": "x"}).text,
              c.delete("/api/ai/credentials/openai-compatible").text]
    for b in bodies:
        assert GOOD_KEY not in b and "ENVSECRET" not in b


def test_environment_key_detected_as_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-ENVSECRET-abcdefghijklmnop")
    row = next(p for p in _client().get("/api/ai/status").json()["providers"]
               if p["id"] == "anthropic")
    assert row["configured"] is True and row["source"] == "environment"


def test_unusable_keyring_falls_back_to_memory(stub_llm):
    keyring.set_keyring(keyring_fail.Keyring())
    base, _ = stub_llm
    c = _client()
    r = _save(c, base, GOOD_KEY)
    assert r.status_code == 200 and r.json()["source"] == "memory"
    assert next(p for p in c.get("/api/ai/status").json()["providers"]
                if p["id"] == "openai-compatible")["configured"] is True


def test_delete_forgets_the_key(stub_llm, _isolate):
    base, _ = stub_llm
    c = _client()
    assert _save(c, base, GOOD_KEY).status_code == 200
    assert c.delete("/api/ai/credentials/openai-compatible").status_code == 200
    assert not _isolate.store
    assert next(p for p in c.get("/api/ai/status").json()["providers"]
                if p["id"] == "openai-compatible")["configured"] is False


def test_select_requires_credentials(stub_llm):
    c = _client()
    assert c.post("/api/ai/select", json={"provider": "anthropic", "model": "m"}).status_code == 409


# --- hosted bind: memory only ----------------------------------------------


def test_hosted_bind_never_touches_keyring_or_disk(_isolate, tmp_path):
    ai_auth.save_credential("anthropic", "sk-ant-HOSTEDSECRET-abcdefghijklmnop", None,
                            mode="memory", session="tab-a")
    ai_auth.set_selection("anthropic", "m", mode="memory", session="tab-a")
    assert _isolate.touched == 0
    assert not (tmp_path / "xdg").exists()
    assert ai_auth.get_credential("anthropic", mode="memory", session="tab-a").api_key
    # per-session isolation
    assert ai_auth.get_credential("anthropic", mode="memory", session="tab-b") is None
    assert ai_auth.get_selection(mode="memory", session="tab-b") is None


def test_hosted_route_uses_memory_mode(monkeypatch, _isolate, tmp_path):
    async def ok(provider, model, cred):
        return None
    monkeypatch.setattr(ai_auth, "check_key", ok)   # the check isn't the proposition here
    c = _client("0.0.0.0")
    r = c.post("/api/ai/credentials",
               json={"provider": "anthropic", "model": "m", "api_key": "sk-ant-abcdefghijklmnopqrstu"},
               headers={"X-VW-Session": "tab-a"})
    assert r.status_code == 200 and r.json()["source"] == "memory"
    assert c.get("/api/ai/status", headers={"X-VW-Session": "tab-a"}).json()["storage_mode"] == "memory"
    assert _isolate.touched == 0 and not (tmp_path / "xdg").exists()
    other = c.get("/api/ai/status", headers={"X-VW-Session": "tab-b"}).json()
    assert next(p for p in other["providers"] if p["id"] == "anthropic")["configured"] is False


@pytest.mark.parametrize("url", [
    "http://example.com/v1",            # not https
    "https://127.0.0.1/v1",             # loopback
    "https://169.254.169.254/latest",   # cloud metadata
    "https://10.0.0.5/v1",              # private
])
def test_hosted_rejects_ssrf_base_urls(url):
    with pytest.raises(APIError) as e:
        ai_auth.validate_request("openai-compatible", None, url, mode="memory")
    assert e.value.status_code == 422


def test_hosted_accepts_public_https_base_url():
    assert ai_auth.validate_request("openai-compatible", None, "https://8.8.8.8/v1/",
                                    mode="memory") == (None, "https://8.8.8.8/v1")


def test_local_allows_loopback_http_base_url():
    assert ai_auth.validate_request("openai-compatible", None, "http://127.0.0.1:11434/v1",
                                    mode="keyring")[1] == "http://127.0.0.1:11434/v1"


@pytest.mark.parametrize("provider,key,url", [
    ("nope", "k", None), ("anthropic", None, None), ("anthropic", "k", "http://x"),
    ("openai-compatible", None, None), ("bedrock", "k", None),
])
def test_validate_request_rejects_bad_shapes(provider, key, url):
    with pytest.raises(APIError) as e:
        ai_auth.validate_request(provider, key, url, mode="keyring")
    assert e.value.status_code == 422


# --- masking + missing extra -----------------------------------------------


def test_mask_key_scrubs_shapes_and_exact_values_idempotently():
    t = ("bad key sk-ant-api03-ABCDEFGHIJKLMNOP1234, AIzaSyABCDEFGHIJKLMNOPQRSTUV, "
         "Authorization: Bearer abcdefghijklmnopqrstuvwx, custom-opaque-secret")
    out = ai_auth.mask_key(t, ("custom-opaque-secret",))
    for leaked in ("ABCDEFGH", "AIzaSy", "abcdefghijkl", "custom-opaque-secret"):
        assert leaked not in out
    assert ai_auth.mask_key(out) == out


def test_missing_extra_is_503_but_status_still_answers(monkeypatch, stub_llm):
    monkeypatch.setitem(sys.modules, "pydantic_ai", None)   # a real ImportError on import
    base, _ = stub_llm
    c = _client()
    assert c.get("/api/ai/status").json()["available"] is False
    r = _save(c, base, GOOD_KEY)
    assert r.status_code == 503
    assert r.json()["error"] == ai_auth.INSTALL_HINT
    assert c.post("/api/ai/select", json={"provider": "openai", "model": "m"}).status_code == 503


def test_readonly_server_keeps_the_ai_routes(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_READONLY", "1")
    app = appmod.create_app()
    paths = {r.path for r in app.router.routes}
    assert {"/api/ai/status", "/api/ai/credentials", "/api/ai/credentials/{provider}",
            "/api/ai/select"} <= paths

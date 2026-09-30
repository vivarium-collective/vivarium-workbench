"""Providers marimo offers that the workbench now has too: Ollama (local, no key, default
base URL, model discovery) and OpenCode Go (OpenAI-compatible, fixed base URL, key).

Real: the app + middleware, pydantic-ai + the OpenAI SDK, httpx discovery, keyring API.
Stubbed: only the remote LLM/model-list servers (local HTTP servers). One test also probes
the REAL opencode.ai /models endpoint (skipped when offline).
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("pydantic_ai")
keyring = pytest.importorskip("keyring")
from fastapi.testclient import TestClient  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_auth  # noqa: E402
from vivarium_workbench.lib.errors import APIError  # noqa: E402

OC_KEY = "sk-oc-OPENCODEKEY-0123456789abcdefghij"


class _MemKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, s, u):
        return self.store.get((s, u))

    def set_password(self, s, u, p):
        self.store[(s, u)] = p

    def delete_password(self, s, u):
        self.store.pop((s, u), None)


class _Server:
    """A local server speaking just enough of Ollama + OpenAI-compatible to be discovered
    and to answer a 1-token completion. Records (method, path, Authorization)."""

    def __init__(self, require_key=None):
        self.seen = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                srv.seen.append(("GET", self.path, self.headers.get("Authorization", "")))
                if self.path == "/api/tags":
                    return self._send(200, {"models": [{"name": "qwen2.5-coder:7b"}, {"name": "llama3.1:8b"}]})
                if self.path == "/v1/models":
                    return self._send(200, {"object": "list", "data": [{"id": "zeta"}, {"id": "alpha"}]})
                self._send(404, {"error": "nope"})

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                auth = self.headers.get("Authorization", "")
                srv.seen.append(("POST", self.path, auth))
                if require_key and auth != f"Bearer {require_key}":
                    return self._send(401, {"error": {"message": "bad key " + auth[7:]}})
                self._send(200, {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
                                 "choices": [{"index": 0, "finish_reason": "length",
                                              "message": {"role": "assistant", "content": "p"}}],
                                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.root = f"http://127.0.0.1:{self.httpd.server_port}"
        self.v1 = self.root + "/v1"

    def close(self):
        self.httpd.shutdown()


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
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
def srv():
    s = _Server()
    yield s
    s.close()


def _client(bind="127.0.0.1"):
    app = appmod.create_app()
    app.state.bind_host = bind
    return TestClient(app, base_url="http://127.0.0.1:8000")


# --- validation ---------------------------------------------------------------


def test_provider_list_follows_marimos_order_and_includes_the_new_ones():
    assert ai_auth.PROVIDERS == ("openai", "anthropic", "google", "ollama", "opencode",
                                 "bedrock", "openai-compatible")


def test_ollama_needs_no_key_and_defaults_its_base_url():
    assert ai_auth.validate_request("ollama", None, None, mode="keyring") == (None, ai_auth.OLLAMA_DEFAULT)
    assert ai_auth.validate_request("ollama", "", "http://127.0.0.1:11500/v1/", mode="keyring") == \
        (None, "http://127.0.0.1:11500/v1")
    assert ai_auth.OLLAMA_DEFAULT == "http://localhost:11434/v1"      # marimo's placeholder


def test_ollama_on_a_hosted_server_must_be_public_https():
    with pytest.raises(APIError) as e:
        ai_auth.validate_request("ollama", None, None, mode="memory")     # the http://localhost default
    assert e.value.status_code == 422


def test_opencode_needs_a_key_and_its_base_url_is_fixed():
    assert ai_auth.validate_request("opencode", "k", None, mode="keyring") == ("k", None)
    for key, url in [(None, None), ("k", "https://evil.example/v1")]:
        with pytest.raises(APIError) as e:
            ai_auth.validate_request("opencode", key, url, mode="keyring")
        assert e.value.status_code == 422
    assert ai_auth.OPENCODE_BASE == "https://opencode.ai/zen/go/v1"      # marimo's "OpenCode Go"


def test_build_model_wires_the_right_endpoints():
    from pydantic_ai.models.openai import OpenAIChatModel
    m = ai_auth.build_model("ollama", "qwen2.5-coder:7b", ai_auth.Credential(None, "http://localhost:11434/v1"))
    assert isinstance(m, OpenAIChatModel) and str(m.client.base_url).rstrip("/") == "http://localhost:11434/v1"
    o = ai_auth.build_model("opencode", "minimax-m3", ai_auth.Credential(OC_KEY, None))
    assert str(o.client.base_url).rstrip("/") == ai_auth.OPENCODE_BASE and o.client.api_key == OC_KEY


# --- ollama: save (real 1-token check), status, discovery -----------------------


def test_ollama_saves_without_a_key_after_a_real_check(srv, _iso):
    c = _client()
    r = c.post("/api/ai/credentials", json={"provider": "ollama", "model": "qwen2.5-coder:7b", "base_url": srv.v1})
    assert r.status_code == 200, r.text
    posts = [s for s in srv.seen if s[0] == "POST"]
    assert len(posts) == 1 and posts[0][1] == "/v1/chat/completions"    # exactly one real check request
    st = c.get("/api/ai/status").json()
    row = next(p for p in st["providers"] if p["id"] == "ollama")
    assert row["configured"] is True and row["base_url"] == srv.v1
    assert st["selected"] == {"provider": "ollama", "model": "qwen2.5-coder:7b"}
    assert "OPENAI" not in json.dumps(st)


def test_ollama_that_is_not_running_is_a_readable_502(_iso):
    c = _client()
    r = c.post("/api/ai/credentials", json={"provider": "ollama", "model": "m", "base_url": "http://127.0.0.1:9/v1"})
    assert r.status_code == 502 and "ollama serve" in r.json()["error"]


def test_ollama_discovery_lists_tags_sorted(srv):
    r = _client().get("/api/ai/models", params={"provider": "ollama", "base_url": srv.v1})
    assert r.status_code == 200, r.text
    assert r.json() == {"models": ["llama3.1:8b", "qwen2.5-coder:7b"], "source": srv.root + "/api/tags"}


def test_discovery_uses_the_saved_base_url_when_none_is_given(srv):
    c = _client()
    ai_auth.save_credential("ollama", None, srv.v1, mode="keyring", session=None)
    assert c.get("/api/ai/models", params={"provider": "ollama"}).json()["models"][0] == "llama3.1:8b"


def test_openai_compatible_discovery_sends_the_saved_key_to_its_own_endpoint_only():
    s = _Server()
    try:
        ai_auth.save_credential("openai-compatible", "sk-k-SAVEDKEY-0123456789abcdefgh", s.v1, mode="keyring", session=None)
        c = _client()
        r = c.get("/api/ai/models", params={"provider": "openai-compatible"})
        assert r.json()["models"] == ["alpha", "zeta"]
        assert ("GET", "/v1/models", "Bearer sk-k-SAVEDKEY-0123456789abcdefgh") in s.seen
        # a DIFFERENT url passed by the caller must not receive the saved key
        other = _Server()
        try:
            c.get("/api/ai/models", params={"provider": "openai-compatible", "base_url": other.v1})
            assert all(a == "" for _, _, a in other.seen)
        finally:
            other.close()
    finally:
        s.close()


def test_opencode_discovery_needs_no_key(srv, monkeypatch):
    monkeypatch.setattr(ai_auth, "OPENCODE_BASE", srv.v1)
    r = _client().get("/api/ai/models", params={"provider": "opencode"})
    assert r.status_code == 200 and r.json()["models"] == ["alpha", "zeta"]
    assert all(a == "" for _, _, a in srv.seen)


def test_discovery_errors_are_typed_and_ssrf_guarded():
    c = _client()
    assert c.get("/api/ai/models", params={"provider": "anthropic"}).status_code == 404
    assert c.get("/api/ai/models", params={"provider": "nope"}).status_code == 422
    assert c.get("/api/ai/models", params={"provider": "ollama", "base_url": "http://127.0.0.1:9/v1"}).status_code == 502
    hosted = _client("0.0.0.0")
    for url in ("http://127.0.0.1:11434/v1", "https://169.254.169.254/v1"):
        r = hosted.get("/api/ai/models", params={"provider": "ollama", "base_url": url})
        assert r.status_code == 422, (url, r.text)


# --- opencode: save/re-save with the fixed base URL -----------------------------


def test_opencode_saves_with_a_bearer_key_and_resaves_without_retyping_it(monkeypatch):
    s = _Server(require_key=OC_KEY)
    monkeypatch.setattr(ai_auth, "OPENCODE_BASE", s.v1)
    try:
        c = _client()
        body = {"provider": "opencode", "model": "minimax-m3"}
        assert c.post("/api/ai/credentials", json={**body, "api_key": OC_KEY}).status_code == 200
        assert c.post("/api/ai/credentials", json=body).status_code == 200          # key reused (fixed base)
        assert [a for m, p, a in s.seen if m == "POST"] == [f"Bearer {OC_KEY}"] * 2
        bad = c.post("/api/ai/credentials", json={**body, "api_key": "sk-oc-WRONGWRONGWRONGWRONG1234"})
        assert bad.status_code == 401 and "WRONGWRONG" not in bad.text
        assert OC_KEY not in c.get("/api/ai/status").text
    finally:
        s.close()


def test_the_real_opencode_go_models_endpoint_is_reachable_and_public():
    import httpx
    try:
        r = httpx.get(ai_auth.OPENCODE_BASE + "/models", timeout=8)
    except httpx.HTTPError:
        pytest.skip("offline")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert ids and all(isinstance(i, str) for i in ids)

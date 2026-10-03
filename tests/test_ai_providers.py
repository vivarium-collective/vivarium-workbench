"""Providers marimo offers that the workbench now has too: Ollama (local, no key, default
base URL) and OpenCode Go (OpenAI-compatible, fixed base URL, key).

Real: the app + middleware, pydantic-ai + the OpenAI SDK, keyring API.
Stubbed: only the remote LLM (a local HTTP server).
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
        self.reads = []

    def get_password(self, s, u):
        self.reads.append(u)
        return self.store.get((s, u))

    def set_password(self, s, u, p):
        self.store[(s, u)] = p

    def delete_password(self, s, u):
        self.store.pop((s, u), None)


class _Server:
    """A local server speaking just enough OpenAI-compatible to answer a 1-token completion.
    Records (method, path, Authorization)."""

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
                    return self._send(200, {"models": [{"name": "qwen2.5-coder:7b"}, {"name": "llama3.1:8b"}, {"name": 5}]})
                if self.path == "/big/api/tags":
                    return self._send(200, {"models": [{"name": "x" * 2_000_000}]})
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
    ai_auth._KR_CACHE.clear()
    ai_auth._KR_FAILED.clear()
    ai_auth._SELECTION.clear()
    prev = keyring.get_keyring()
    backend = _MemKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(prev)
    ai_auth._MEMORY.clear()
    ai_auth._KR_CACHE.clear()
    ai_auth._KR_FAILED.clear()
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
    # marimo's order, then claude-code (not marimo's: the user's own `claude` CLI), then the generic entry
    assert ai_auth.PROVIDERS == ("openai", "anthropic", "google", "ollama", "opencode",
                                 "bedrock", "claude-code", "openai-compatible")


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


# --- keychain hygiene: no secret, no keychain access (macOS prompts on every foreign read) ------


def test_ollama_endpoint_lives_in_ai_yaml_never_the_keychain(srv, _iso):
    c = _client()
    r = c.post("/api/ai/credentials", json={"provider": "ollama", "model": "m", "base_url": srv.v1})
    assert r.status_code == 200 and r.json()["source"] == "config"
    assert _iso.store == {} and _iso.reads == []                       # the keychain was never touched
    assert srv.v1 in ai_auth.selection_path().read_text()               # a URL is not a secret
    row = next(p for p in c.get("/api/ai/status").json()["providers"] if p["id"] == "ollama")
    assert row["configured"] is True and row["source"] == "config" and row["base_url"] == srv.v1
    assert c.get("/api/ai/status").json()["selected"] == {"provider": "ollama", "model": "m"}, "selection kept alongside the endpoint"
    assert c.delete("/api/ai/credentials/ollama").status_code == 200
    assert next(p for p in c.get("/api/ai/status").json()["providers"] if p["id"] == "ollama")["configured"] is False
    assert _iso.store == {} and _iso.reads == []


def test_status_reads_only_the_keychain_entries_this_app_saved(srv, _iso, monkeypatch):
    c = _client()
    c.get("/api/ai/status")
    assert _iso.reads == [], "a fresh install must not touch the keychain at all"
    s = _Server(require_key=OC_KEY)
    monkeypatch.setattr(ai_auth, "OPENCODE_BASE", s.v1)
    try:
        assert c.post("/api/ai/credentials", json={"provider": "opencode", "model": "m", "api_key": OC_KEY}).status_code == 200
    finally:
        s.close()
    assert ("vivarium-workbench-llm", "opencode") in _iso.store
    _iso.reads.clear()
    st = c.get("/api/ai/status").json()
    assert set(_iso.reads) == {"opencode"}, _iso.reads                     # only the provider it saved
    assert next(p for p in st["providers"] if p["id"] == "opencode")["source"] == "keyring"
    assert c.delete("/api/ai/credentials/opencode").status_code == 200
    assert _iso.store == {} and "opencode" not in (ai_auth._read_cfg().get("keyring") or [])


def test_a_keychain_entry_is_read_once_per_process_and_a_refusal_is_not_retried(_iso, monkeypatch):
    ai_auth.save_credential("openai-compatible", "sk-k-SAVEDKEY-0123456789abcdefgh", "https://example.com/v1",
                            mode="keyring", session=None)
    ai_auth._KR_CACHE.clear()
    _iso.reads.clear()
    for _ in range(5):                                            # the UI asks for status several times per page load
        assert ai_auth.get_credential("openai-compatible", mode="keyring", session=None).source == "keyring"
    assert _iso.reads == ["openai-compatible"], "one keychain read, not one per status call"

    # the user refuses the macOS prompt -> the read raises; do not re-prompt on every poll
    ai_auth._KR_CACHE.clear()
    _iso.reads.clear()

    def refused(s, u):
        _iso.reads.append(u)
        raise PermissionError("user denied")
    monkeypatch.setattr(_iso, "get_password", refused)
    for _ in range(5):
        assert ai_auth.get_credential("openai-compatible", mode="keyring", session=None) is None
    assert _iso.reads == ["openai-compatible"], "a refusal is remembered for KR_RETRY_S"



def test_a_slow_keychain_does_not_freeze_the_event_loop(monkeypatch):
    """macOS shows a prompt and WAITS on a foreign keychain read; that must not stall every other request."""
    import asyncio
    import time

    from vivarium_workbench.lib import ai_views
    from vivarium_workbench.lib.models import AiCredentialsRequest

    def slow(*a, **k):
        time.sleep(0.4)
        return None
    monkeypatch.setattr(ai_auth, "get_credential", slow)

    async def go():
        gaps, stop = [], False

        async def ticker():
            last = time.monotonic()
            while not stop:
                await asyncio.sleep(0.02)
                now = time.monotonic()
                gaps.append(now - last)
                last = now
        t = asyncio.create_task(ticker())
        with pytest.raises(APIError):          # opencode without a key: 422, after the (slow) keychain lookup
            await ai_views.ai_save_credentials(AiCredentialsRequest(provider="opencode", model="m"), "keyring", None)
        stop = True
        await t
        return max(gaps)
    assert asyncio.run(go()) < 0.2, "the event loop was blocked by the keychain lookup"


# --- installed Ollama models: what the dropdown lists for Ollama --------------------------------


def test_ollama_models_are_the_installed_ones_sorted_and_no_key_is_sent(srv):
    r = _client().post("/api/ai/ollama-models", json={"base_url": srv.v1})
    assert r.status_code == 200, r.text
    assert r.json() == {"models": ["llama3.1:8b", "qwen2.5-coder:7b"], "source": srv.root + "/api/tags"}
    assert srv.seen == [("GET", "/api/tags", "")]                       # junk entries dropped, no Authorization


def test_ollama_models_use_the_saved_endpoint_when_none_is_given(srv):
    ai_auth.save_credential("ollama", None, srv.v1, mode="keyring", session=None)
    assert _client().post("/api/ai/ollama-models", json={}).json()["models"][0] == "llama3.1:8b"


def test_a_cross_site_page_cannot_make_the_server_probe_a_host(srv):
    """S-13: a GET carries no Origin, so any web page could trigger the lookup blind. It is a POST now, and the
    CSRF guard refuses a cross-site one before anything is fetched."""
    c = _client()
    r = c.post("/api/ai/ollama-models", json={"base_url": srv.v1}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403, r.text
    assert srv.seen == []                                               # nothing was contacted
    assert c.get("/api/ai/ollama-models", params={"base_url": srv.v1}).status_code in (404, 405)   # no GET route any more
    assert srv.seen == []
    ok = c.post("/api/ai/ollama-models", json={"base_url": srv.v1}, headers={"Origin": "http://127.0.0.1:8000"})
    assert ok.status_code == 200 and srv.seen                           # the page's own origin still works


def test_ollama_not_running_is_a_readable_502():
    r = _client().post("/api/ai/ollama-models", json={"base_url": "http://127.0.0.1:9/v1"})
    assert r.status_code == 502 and "ollama serve" in r.json()["error"]


def test_ollama_models_reject_an_oversized_reply(srv):
    r = _client().post("/api/ai/ollama-models", json={"base_url": srv.v1.replace("/v1", "/big/v1")})
    assert r.status_code == 502 and "too much data" in r.json()["error"]


def test_ollama_models_on_a_hosted_server_are_ssrf_guarded():
    hosted = _client("0.0.0.0")
    for url in ("http://127.0.0.1:11434/v1", "https://169.254.169.254/v1", "https://[::1]/v1"):
        r = hosted.post("/api/ai/ollama-models", json={"base_url": url})
        assert r.status_code == 422, (url, r.text)


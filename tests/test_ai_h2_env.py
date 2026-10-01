"""S-11: a credential in the *server's* environment must never reach an endpoint the *user* names.

Real here: pydantic-ai's provider clients and the HTTP they send, captured by a local endpoint. Stubbed: the model
behind that endpoint.
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("pydantic_ai")
from vivarium_workbench.lib import ai_auth  # noqa: E402

ENV = {
    "OLLAMA_API_KEY": "ENV-OLLAMA-SECRET",
    "OPENAI_API_KEY": "ENV-OPENAI-SECRET",
    "OPENAI_ORG_ID": "ENV-ORG-SECRET",
    "OPENAI_PROJECT_ID": "ENV-PROJECT-SECRET",
    "OPENAI_CUSTOM_HEADERS": "X-Env-Leak: ENV-CUSTOM-SECRET",
}


@pytest.fixture
def endpoint():
    seen: list[dict[str, str]] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            seen.append({k.lower(): v for k, v in self.headers.items()})
            raw = json.dumps({"id": "c", "object": "chat.completion", "created": 0, "model": "m",
                              "choices": [{"index": 0, "finish_reason": "length",
                                           "message": {"role": "assistant", "content": "p"}}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1", seen
    srv.shutdown()


@pytest.mark.parametrize("provider, key", [("ollama", None), ("openai-compatible", None), ("openai-compatible", "user-key")])
def test_server_env_credentials_are_not_sent_to_a_user_supplied_endpoint(endpoint, monkeypatch, provider, key):
    url, seen = endpoint
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    asyncio.run(ai_auth.check_key(provider, "m", ai_auth.Credential(api_key=key, base_url=url)))
    assert seen, "the endpoint was never reached"
    sent = json.dumps(seen)
    for v in ENV.values():
        assert v not in sent, f"{v} was sent to the user-supplied endpoint"
    assert not seen[0].get("openai-organization") and not seen[0].get("openai-project")
    if key:
        assert seen[0]["authorization"] == f"Bearer {key}"

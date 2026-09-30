"""Adversarial-review findings for the built-in chat (F1–F7), each reproduced as a test.

Real here: the FastAPI app + middleware + live OpenAPI, the tools, keyring/audit
files, the filesystem. Stubbed: only the remote LLM (``FunctionModel``).
"""
import asyncio
import contextlib
import json
import os
import stat

import httpx
import pytest

pytest.importorskip("pydantic_ai")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic_ai import RunContext  # noqa: E402
from pydantic_ai.exceptions import ModelHTTPError  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_auth, ai_tools  # noqa: E402
from vivarium_workbench.lib.errors import APIError  # noqa: E402

H = {"X-VW-Session": "tab-1"}
ENV_KEY = "sk-ant-ENVSECRET-abcdefghijklmnopqrstuv"


def _oid(app, method, path):
    return app.openapi()["paths"][path][method]["operationId"]


def _make_app(tmp_path, bind_host="0.0.0.0"):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: hard\n")
    (ws / ".pbg").mkdir()
    app = appmod.create_app()
    app.state.bind_host = bind_host
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    return app, ws


def _ctx(deps, *, approved=False, call_id="call-1"):
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage(), tool_call_approved=approved,
                      tool_call_id=call_id)


@contextlib.asynccontextmanager
async def _deps(app, ws, session="tab-1"):
    d = ai_tools.ChatDeps(app=app, client=ai_tools.make_client(app), ws_root=ws,
                          session_key=session, provider="p", model="m")
    try:
        yield d
    finally:
        await d.client.aclose()


def _audit(ws):
    p = ai_tools.audit_path(ws)
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for e in list(ai_auth.ENV_KEYS.values()) + ["VIVARIUM_WORKBENCH_CHAT_ALLOW_SERVER_CREDENTIALS",
                                                "VIVARIUM_WORKBENCH_TRUST_PROXY",
                                                "VIVARIUM_WORKBENCH_ALLOWED_ORIGINS"]:
        monkeypatch.delenv(e, raising=False)
    ai_auth._MEMORY.clear()
    ai_auth._KR_CACHE.clear()
    ai_auth._KR_FAILED.clear()
    ai_auth._SELECTION.clear()
    yield
    ai_auth._MEMORY.clear()
    ai_auth._KR_CACHE.clear()
    ai_auth._KR_FAILED.clear()
    ai_auth._SELECTION.clear()


# --- F1: the audit log must not carry the raw session key -------------------


def test_audit_never_contains_the_raw_session_key_even_when_served(tmp_path):
    app, ws = _make_app(tmp_path)
    raw = "victim-session-KEY-123456"
    oid = _oid(app, "post", "/api/study-create")

    async def go():
        async with _deps(app, ws, session=raw) as d:
            await ai_tools.call_operation(_ctx(d, approved=True), oid, body={"name": "s1"})

    asyncio.run(go())
    text = ai_tools.audit_path(ws).read_text()
    assert raw not in text
    assert all(len(r["session"]) == 12 for r in _audit(ws))
    # ...and the file is reachable through the workspace catch-all, so what it holds is public
    served = TestClient(app).get("/.pbg/ai-actions.jsonl")
    assert raw not in served.text


# --- F2: hosted servers must not hand ambient credentials to anonymous sessions


def test_memory_mode_ignores_env_and_aws_credentials_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)
    monkeypatch.setattr(ai_auth, "_aws_credentials_present", lambda: True)
    assert ai_auth.get_credential("anthropic", mode="memory", session="anon") is None
    assert ai_auth.get_credential("bedrock", mode="memory", session="anon") is None
    # a loopback (keyring) server is a single-user machine: ambient credentials stay convenient
    assert ai_auth.get_credential("anthropic", mode="keyring", session=None).source == "environment"
    assert ai_auth.get_credential("bedrock", mode="keyring", session=None).source == "aws"


def test_operator_can_opt_in_to_server_credentials_on_a_hosted_server(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_ALLOW_SERVER_CREDENTIALS", "1")
    assert ai_auth.get_credential("anthropic", mode="memory", session="anon").source == "environment"


def test_anonymous_session_cannot_select_the_servers_env_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)
    app, _ = _make_app(tmp_path, bind_host="0.0.0.0")
    c = TestClient(app)
    r = c.post("/api/ai/select", json={"provider": "anthropic", "model": "claude-opus-5-5"},
               headers={"X-VW-Session": "anon-visitor-1"})
    assert r.status_code == 409
    st = c.get("/api/ai/status", headers={"X-VW-Session": "anon-visitor-1"}).json()
    assert next(p for p in st["providers"] if p["id"] == "anthropic")["configured"] is False
    assert st["selected"] is None


# --- F3: approvals are single-use; an error after an approved mutation checkpoints


def _fake_llm(app, fail_after_tool):
    def decide(messages):
        last = messages[-1]
        if any(isinstance(p, ToolReturnPart) for p in getattr(last, "parts", [])):
            if fail_after_tool["on"]:
                raise ModelHTTPError(529, "fm", {"error": "overloaded"})
            return ("text", "Done.")
        prompt = ""
        for m in reversed(messages):
            for p in getattr(m, "parts", []):
                if isinstance(p, UserPromptPart):
                    prompt = str(p.content)
                    break
            if prompt:
                break
        if "list" in prompt:
            return ("tool", {"operation_id": _oid(app, "get", "/api/investigations")})
        return ("tool", {"operation_id": _oid(app, "post", "/api/study-create"), "body": {"name": "made"}})

    def function(messages, info):
        kind, val = decide(messages)
        return ModelResponse(parts=[TextPart(val)] if kind == "text" else [ToolCallPart("call_operation", val)])

    async def stream(messages, info):
        kind, val = decide(messages)
        if kind == "text":
            yield val
        else:
            yield {0: DeltaToolCall(name="call_operation", json_args=json.dumps(val), tool_call_id="call-x")}

    return FunctionModel(function, stream_function=stream, model_name="fm")


@pytest.fixture
def chat(tmp_path, monkeypatch):
    app, ws = _make_app(tmp_path, bind_host="0.0.0.0")
    flag = {"on": False}
    ai_auth.save_credential("anthropic", "sk-ant-abcdefghijklmnopqrstuvwx", None, mode="memory", session="tab-1")
    ai_auth.set_selection("anthropic", "fm", mode="memory", session="tab-1")
    monkeypatch.setattr(ai_auth, "build_model", lambda *a, **k: _fake_llm(app, flag))
    return TestClient(app), ws, flag


def _turn(client, **body):
    body.setdefault("messages", [])
    r = client.post("/api/chat/turn", json=body, headers=H)
    assert r.status_code == 200, r.text
    return [json.loads(x) for x in r.text.splitlines()]


def _pause(client):
    f1 = _turn(client, prompt="create a study")
    return f1[1]["tool_call_id"], f1[-1]["messages"]


def test_replaying_an_approval_does_not_run_the_mutation_again(chat):
    client, ws, _ = chat
    cid, msgs = _pause(client)
    body = {"messages": msgs, "deferred_results": {"approvals": {cid: True}}}
    first = _turn(client, **body)
    assert next(f for f in first if f["type"] == "tool-result")["content"]["status"] == 200
    replay = _turn(client, **body)                       # the very same request, again
    res = next(f for f in replay if f["type"] == "tool-result")["content"]
    assert "already executed" in res["error"]
    phases = [r["phase"] for r in _audit(ws)]
    assert phases == ["intent", "result"]                # exactly one execution recorded


def test_error_after_an_approved_mutation_checkpoints_the_transcript(chat):
    client, ws, fail = chat
    cid, msgs = _pause(client)
    fail["on"] = True                                    # the provider dies right after the tool ran
    frames = _turn(client, messages=msgs, deferred_results={"approvals": {cid: True}})
    types = [f["type"] for f in frames]
    assert "tool-result" in types and types[-2:] == ["error", "done"]
    done = frames[-1]
    assert done["incomplete"] is True and done["pending_approval"] is False
    assert (ws / "studies" / "made").exists()
    # the checkpoint is usable: the next prompt works instead of "unprocessed tool calls"
    fail["on"] = False
    nxt = _turn(client, messages=done["messages"], prompt="list the studies")
    assert nxt[-1]["type"] == "done" and "error" not in [f["type"] for f in nxt]
    ModelMessagesTypeAdapter.validate_python(nxt[-1]["messages"])


# --- F4: audit is intent-first and never raises after a mutation ran ---------


def _slow_app(tmp_path, marker):
    import time
    app = FastAPI()

    @app.post("/api/slow", operation_id="slow_post")
    def slow():                                          # a sync handler: runs to completion in a thread
        time.sleep(0.6)
        marker.write_text("ran")
        return {"ok": True}

    return app


def test_cancelled_mutation_is_still_audited(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    marker = tmp_path / "marker"
    app = _slow_app(tmp_path, marker)

    async def go():
        async with _deps(app, ws) as d:
            t = asyncio.create_task(ai_tools.call_operation(_ctx(d, approved=True), "slow_post"))
            await asyncio.sleep(0.2)
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
            await asyncio.sleep(0.9)

    asyncio.run(go())
    assert marker.exists()                               # the mutation really did run
    recs = _audit(ws)
    assert [r["phase"] for r in recs] == ["intent", "result"]
    assert recs[1]["status"] is None and recs[1]["outcome"] == "interrupted"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_audit_log_refuses_the_change_instead_of_running_it_unaudited(tmp_path):
    app, ws = _make_app(tmp_path)
    oid = _oid(app, "post", "/api/study-create")
    os.chmod(ws / ".pbg", stat.S_IRUSR | stat.S_IXUSR)
    try:
        async def go():
            async with _deps(app, ws) as d:
                return await ai_tools.call_operation(_ctx(d, approved=True), oid, body={"name": "nope"})
        out = asyncio.run(go())
    finally:
        os.chmod(ws / ".pbg", stat.S_IRWXU)
    assert "audit log unavailable" in out["error"]
    assert not (ws / "studies" / "nope").exists()


def test_failure_to_record_the_result_is_a_warning_not_an_exception(tmp_path, monkeypatch):
    app, ws = _make_app(tmp_path)
    oid = _oid(app, "post", "/api/study-create")
    real = ai_tools.append_audit

    def flaky(ws_root, record):
        if record.get("phase") == "result":
            raise OSError("disk full")
        return real(ws_root, record)

    monkeypatch.setattr(ai_tools, "append_audit", flaky)

    async def go():
        async with _deps(app, ws) as d:
            return await ai_tools.call_operation(_ctx(d, approved=True), oid, body={"name": "kept"})

    out = asyncio.run(go())
    assert out["status"] == 200 and "disk full" in out["audit_warning"]
    assert (ws / "studies" / "kept").exists()


# --- F5: exclusion policy -----------------------------------------------------


@pytest.mark.parametrize("method,path", [
    # NOTE registry installs (catalog-install/uninstall, import-install, system-deps-install) used to be
    # here; they are ordinary user actions and are now reachable behind approval
    # (tests/test_ai_automation.py::test_registry_installs_are_now_reachable...).
    ("get", "/api/workspaces"), ("post", "/api/work-create-pr"), ("get", "/api/simulation-run-download"),
    ("get", "/api/study-analysis-zip"), ("get", "/api/composite-run/{run_id}/download"),
    ("get", "/api/study-export"),
])
def test_host_level_remote_and_binary_operations_are_excluded(tmp_path, method, path):
    app, _ = _make_app(tmp_path)
    assert method in app.openapi()["paths"][path]
    served = {(e["method"].lower(), e["path"]) for e in ai_tools.build_index(app).values()}
    assert (method, path) not in served


def test_downloads_tag_excludes_reads_only_so_figures_build_stays_available(tmp_path):
    app, _ = _make_app(tmp_path)
    served = {(e["method"].lower(), e["path"]) for e in ai_tools.build_index(app).values()}
    assert ("post", "/api/investigation/{slug}/figures-build") in served


# --- F6: DNS rebinding + proxied/unknown binds must not reach the keyring ----


def test_keyring_mode_rejects_non_loopback_host_headers(tmp_path):
    app, _ = _make_app(tmp_path, bind_host="127.0.0.1")
    assert TestClient(app, base_url="http://127.0.0.1:8000").get("/api/ai/status").status_code == 200
    assert TestClient(app, base_url="http://localhost:8000").get("/api/ai/status").status_code == 200
    rebound = TestClient(app, base_url="http://evil.example:8000")
    assert rebound.get("/api/ai/status").status_code == 403
    assert rebound.delete("/api/ai/credentials/anthropic").status_code == 403
    assert rebound.post("/api/chat/turn", json={"prompt": "x", "messages": []}).status_code == 403


def test_proxied_or_unknown_binds_are_treated_as_multi_user(monkeypatch):
    assert ai_auth.storage_mode("127.0.0.1") == "keyring"
    assert ai_auth.storage_mode("127.0.0.1", proxied=True) == "memory"
    assert ai_auth.storage_mode(None) == "memory"


@pytest.mark.parametrize("env,val", [("VIVARIUM_WORKBENCH_TRUST_PROXY", "1"),
                                     ("VIVARIUM_WORKBENCH_ALLOWED_ORIGINS", "https://demo.example.gov")])
def test_proxy_flags_switch_a_loopback_server_to_memory_mode(tmp_path, monkeypatch, env, val):
    monkeypatch.setenv(env, val)
    app, _ = _make_app(tmp_path, bind_host="127.0.0.1")
    st = TestClient(app, base_url="http://127.0.0.1:8000").get("/api/ai/status").json()
    assert st["storage_mode"] == "memory"


def test_base_path_switches_to_memory_mode(tmp_path):
    app, _ = _make_app(tmp_path, bind_host="127.0.0.1")
    app.state.base_path = "/workbench"
    st = TestClient(app, base_url="http://127.0.0.1:8000").get("/api/ai/status").json()
    assert st["storage_mode"] == "memory"


# --- F7 -----------------------------------------------------------------------


@pytest.mark.parametrize("url", ["https://8.8.8.8:99999/v1", "https://8.8.8.8:abc/v1",
                                 "https://[64:ff9b::a9fe:a9fe]/v1"])
def test_bad_ports_and_nat64_are_422_not_500_or_accepted(url):
    with pytest.raises(APIError) as e:
        ai_auth.validate_request("openai-compatible", None, url, mode="memory")
    assert e.value.status_code == 422


def test_oversized_prompt_and_transcript_are_rejected(chat):
    client, _, _ = chat
    r = client.post("/api/chat/turn", json={"messages": [], "prompt": "x" * 400_000}, headers=H)
    assert r.status_code == 422
    r = client.post("/api/chat/turn", json={"messages": [{}] * 2000, "prompt": "hi"}, headers=H)
    assert r.status_code == 422


def test_pydantic_ai_is_capped_below_the_next_major():
    import tomllib
    from pathlib import Path
    deps = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())[
        "project"]["optional-dependencies"]["chat"]
    spec = next(d for d in deps if d.startswith("pydantic-ai-slim"))
    assert "<3" in spec


def test_provider_banner_is_silenced_for_server_logs():
    import importlib
    os.environ.pop("PYDANTIC_AI_NO_BANNER", None)
    from vivarium_workbench.lib import ai_chat
    importlib.reload(ai_chat)
    assert os.environ.get("PYDANTIC_AI_NO_BANNER") == "1"


def test_resaving_openai_compatible_without_a_key_keeps_the_stored_key(tmp_path, monkeypatch):
    """ai_views used to overwrite the credential with api_key=None."""
    from vivarium_workbench.lib import ai_views
    from vivarium_workbench.lib.models import AiCredentialsRequest
    ai_auth.save_credential("openai-compatible", "sk-keep-KEEPKEEPKEEPKEEPKEEP", "http://127.0.0.1:1/v1",
                            mode="memory", session="tab-1")

    async def ok(provider, model, cred):
        assert cred.api_key == "sk-keep-KEEPKEEPKEEPKEEPKEEP"      # the check used the stored key
    monkeypatch.setattr(ai_auth, "check_key", ok)
    monkeypatch.setattr(ai_auth, "_check_base_url", lambda url, mode: url)
    asyncio.run(ai_views.ai_save_credentials(
        AiCredentialsRequest(provider="openai-compatible", model="m", base_url="http://127.0.0.1:1/v1"),
        "memory", "tab-1"))
    assert ai_auth.get_credential("openai-compatible", mode="memory", session="tab-1").api_key == \
        "sk-keep-KEEPKEEPKEEPKEEPKEEP"


def test_httpx_is_used_for_in_process_calls_only():
    """Guard the SSRF surface: the tool client must be ASGI-only (no real network)."""
    c = ai_tools.make_client(FastAPI())
    assert isinstance(c._transport, httpx.ASGITransport)


# =============================================================================
# Turn 2 findings (G1–G6)
# =============================================================================

import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402

keyring = pytest.importorskip("keyring")
from keyring.backends import fail as _keyring_fail  # noqa: E402


class _Recorder:
    """A local OpenAI-compatible endpoint that records every Authorization header."""

    def __init__(self, good_key=None):
        self.auth = []
        rec = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                rec.auth.append(self.headers.get("Authorization", ""))
                ok = good_key is None or self.headers.get("Authorization") == f"Bearer {good_key}"
                body = ({"id": "c", "object": "chat.completion", "created": 0, "model": "m",
                         "choices": [{"index": 0, "finish_reason": "length",
                                      "message": {"role": "assistant", "content": "p"}}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                        if ok else {"error": {"message": "bad key"}})
                raw = json.dumps(body).encode()
                self.send_response(200 if ok else 401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"

    def close(self):
        self.srv.shutdown()


# --- G1: a saved key must never be sent to a *different* endpoint -------------


def test_stored_key_is_not_sent_to_a_changed_base_url(tmp_path):
    KEY = "sk-or-VICTIMKEY-0123456789abcdefghijkl"
    prev = keyring.get_keyring()
    keyring.set_keyring(_keyring_fail.Keyring())          # key lands in memory scope
    a, b = _Recorder(good_key=KEY), _Recorder()
    try:
        app, _ = _make_app(tmp_path, bind_host="127.0.0.1")
        c = TestClient(app, base_url="http://127.0.0.1:8000")
        body = {"provider": "openai-compatible", "model": "m"}
        assert c.post("/api/ai/credentials", json={**body, "base_url": a.url, "api_key": KEY}).status_code == 200
        # same endpoint, no key retyped -> the saved key is reused (and is sent to A only)
        assert c.post("/api/ai/credentials", json={**body, "base_url": a.url}).status_code == 200
        # a DIFFERENT endpoint, no key retyped -> must NOT ship the saved key there
        c.post("/api/ai/credentials", json={**body, "base_url": b.url})
        assert KEY not in "".join(b.auth), f"saved key leaked to another endpoint: {b.auth}"
    finally:
        keyring.set_keyring(prev)
        a.close(); b.close()


# --- G2: a provider that reuses tool_call_ids must not be blocked forever ------


def test_reused_tool_call_id_in_a_later_response_is_not_treated_as_a_replay(tmp_path):
    from datetime import datetime, timedelta, timezone
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    app, ws = _make_app(tmp_path)
    oid = _oid(app, "post", "/api/investigation-create") if "/api/investigation-create" in app.openapi()["paths"] \
        else _oid(app, "post", "/api/study-create")
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def ctx_at(when, name):
        msgs = [ModelResponse(parts=[ToolCallPart("call_operation", {"operation_id": oid, "body": {"name": name}},
                                                  tool_call_id="call_0")], timestamp=when)]
        return RunContext(deps=None, model=TestModel(), usage=RunUsage(), tool_call_approved=True,
                          tool_call_id="call_0", messages=msgs)

    async def go():
        out = []
        async with _deps(app, ws) as d:
            for when, name in ((t0, "same"), (t0, "same"), (t0 + timedelta(days=7), "same")):
                c = ctx_at(when, name)
                c.deps = d
                out.append(await ai_tools.call_operation(c, oid, body={"name": name}))
        return out

    first, replay, later = asyncio.run(go())
    assert first["status"] == 200
    assert "already executed" in replay["error"]          # same response => a real replay
    assert "already executed" not in json.dumps(later)    # a later response reusing the id is a new call


# --- G3/G4: interrupted turns -------------------------------------------------


def test_abandoning_a_turn_between_frames_is_quiet(tmp_path, monkeypatch):
    """A client disconnect finalises the generator in another context; nothing may
    log 'Token was created in a different Context'."""
    from vivarium_workbench.lib import ai_chat
    from vivarium_workbench.lib.models import ChatTurnRequest
    app, ws = _make_app(tmp_path)
    ai_auth.save_credential("anthropic", "sk-ant-abcdefghijklmnopqrstuvwx", None, mode="memory", session="t")
    ai_auth.set_selection("anthropic", "fm", mode="memory", session="t")
    monkeypatch.setattr(ai_auth, "build_model", lambda *a, **k: _fake_llm(app, {"on": False}))
    errors = []

    async def go():
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: errors.append(ctx))
        turn = ai_chat.prepare_turn(app, ChatTurnRequest(prompt="list the studies"), ws, "memory", "t")
        gen = turn.frames()
        await gen.__anext__()                              # one frame, then the client vanishes
        await asyncio.create_task(gen.aclose())            # finalised from a different task/context
        await asyncio.sleep(0.2)

    asyncio.run(go())
    assert errors == []


# --- G5: the banner is silenced before pydantic-ai is first imported anywhere --


def test_banner_is_silenced_by_ai_auth_too():
    import importlib
    os.environ.pop("PYDANTIC_AI_NO_BANNER", None)
    importlib.reload(ai_auth)
    assert os.environ.get("PYDANTIC_AI_NO_BANNER") == "1"


# --- G6: Host gate details ------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.com@localhost", "localhost@evil.com", "@localhost"])
def test_host_with_userinfo_is_rejected(tmp_path, host):
    app, _ = _make_app(tmp_path, bind_host="127.0.0.1")
    r = TestClient(app, base_url="http://127.0.0.1:8000").get("/api/ai/status", headers={"Host": host})
    assert r.status_code == 403


def test_no_operation_shares_the_ai_or_chat_prefix_unexcluded(tmp_path):
    """The exclusion prefixes are `/api/ai/` and `/api/chat/`; a future route named
    `/api/ai-…` would slip past them. If this fails, decide whether it belongs to the model."""
    app, _ = _make_app(tmp_path)
    served = {e["path"] for e in ai_tools.build_index(app).values()}
    assert not any(p.startswith(("/api/ai", "/api/chat")) for p in served)


# =============================================================================
# Turn 3 findings (H1–H4)
# =============================================================================


def test_new_prompt_over_a_dangling_transcript_is_repaired_not_wedged(chat):
    """H2: a transcript ending on an unresolved tool call (a resume that was stopped/lost)
    used to make every later prompt fail with 'unprocessed tool calls'."""
    client, ws, _ = chat
    _cid, msgs = _pause(client)                    # ends on an approval-pending tool call
    frames = _turn(client, messages=msgs, prompt="list the studies")
    types = [f["type"] for f in frames]
    assert "error" not in types and types[-1] == "done"
    assert not (ws / "studies" / "made").exists()   # the interrupted action was NOT executed
    # the model was told the action's outcome is unknown, not silently dropped
    dumped = json.dumps(frames[-1]["messages"])
    assert "outcome is unknown" in dumped


def test_already_executed_refusal_carries_the_recorded_outcome(chat):
    """H3: the retry should tell the model what happened, not just that it happened."""
    client, ws, _ = chat
    cid, msgs = _pause(client)
    body = {"messages": msgs, "deferred_results": {"approvals": {cid: True}}}
    _turn(client, **body)
    replay = _turn(client, **body)
    err = next(f for f in replay if f["type"] == "tool-result")["content"]["error"]
    assert "already executed" in err and "status 200" in err


def test_already_executed_without_a_result_line_says_so(tmp_path):
    app, ws = _make_app(tmp_path)
    oid = _oid(app, "post", "/api/study-create")
    body = {"name": "x"}
    path = "/api/study-create"
    ai_tools.append_audit(ws, {"phase": "intent", "tool_call_id": "c1", "operation_id": oid,
                               "digest": ai_tools._call_digest(oid, path, None, body, "")})

    async def go():
        async with _deps(app, ws) as d:
            return await ai_tools.call_operation(_ctx(d, approved=True, call_id="c1"), oid, body=body)

    out = asyncio.run(go())
    assert "already executed" in out["error"] and "no result" in out["error"]

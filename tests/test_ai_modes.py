"""Chat modes (Manual / Ask / Agent), the optional workspace summary, reasoning frames
and the capabilities endpoint — the controls marimo's chat footer has, made real.

Stubbed: only the remote LLM (``FunctionModel``). Real: app, middleware, tools, OpenAPI.
"""
import asyncio
import json

import pytest

pytest.importorskip("pydantic_ai")
from fastapi.testclient import TestClient  # noqa: E402
from pydantic_ai import RunContext  # noqa: E402
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart  # noqa: E402
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_auth, ai_tools  # noqa: E402

H = {"X-VW-Session": "tab-1"}
SEEN = {"tools": [], "instructions": []}


def _oid(app, method, path):
    return app.openapi()["paths"][path][method]["operationId"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: modes\n")
    (ws / ".pbg").mkdir()
    app = appmod.create_app()
    app.state.bind_host = "0.0.0.0"
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    ai_auth._MEMORY.clear()
    ai_auth._SELECTION.clear()
    SEEN["tools"].clear()
    SEEN["instructions"].clear()
    ai_auth.save_credential("anthropic", "sk-ant-abcdefghijklmnopqrstuvwx", None, mode="memory", session="tab-1")
    ai_auth.set_selection("anthropic", "fm", mode="memory", session="tab-1")

    def decide(messages):
        last = messages[-1]
        if any(isinstance(p, ToolReturnPart) for p in getattr(last, "parts", [])):
            return ("text", "ok")
        prompt = json.dumps([str(getattr(p, "content", "")) for m in messages for p in getattr(m, "parts", [])])
        if "think" in prompt:
            return ("think", "hmm")
        if "create" in prompt:
            return ("tool", {"operation_id": _oid(app, "post", "/api/study-create"), "body": {"name": "m1"}})
        if "list" in prompt:
            return ("tool", {"operation_id": _oid(app, "get", "/api/investigations")})
        return ("text", "hello")

    def function(messages, info):
        SEEN["tools"].append([t.name for t in info.function_tools])
        SEEN["instructions"].append(info.instructions or "")
        kind, val = decide(messages)
        if kind == "tool" and not info.function_tools:      # a real model can't call tools it wasn't offered
            kind, val = "text", "I have no tools in this mode."
        if kind == "tool":
            return ModelResponse(parts=[ToolCallPart("call_operation", val)])
        return ModelResponse(parts=[TextPart(val)])

    async def stream(messages, info):
        SEEN["tools"].append([t.name for t in info.function_tools])
        SEEN["instructions"].append(info.instructions or "")
        kind, val = decide(messages)
        if kind == "tool" and not info.function_tools:
            kind, val = "text", "I have no tools in this mode."
        if kind == "tool":
            yield {0: DeltaToolCall(name="call_operation", json_args=json.dumps(val), tool_call_id="c-1")}
        elif kind == "think":
            yield {0: DeltaThinkingPart(content="pondering ")}
            yield {0: DeltaThinkingPart(content="deeply")}
            yield "answer"
        else:
            yield val

    monkeypatch.setattr(ai_auth, "build_model",
                        lambda *a, **k: FunctionModel(function, stream_function=stream, model_name="fm"))
    yield TestClient(app), ws, app
    ai_auth._MEMORY.clear()
    ai_auth._SELECTION.clear()


def _turn(client, **body):
    body.setdefault("messages", [])
    r = client.post("/api/chat/turn", json=body, headers=H)
    assert r.status_code == 200, r.text
    return [json.loads(x) for x in r.text.splitlines()]


def test_manual_mode_is_pure_chat_with_no_tools(env):
    client, ws, _ = env
    frames = _turn(client, prompt="create a study", mode="manual")
    assert SEEN["tools"][0] == []                        # the model was offered NO tools
    assert "no tools" in SEEN["instructions"][0].lower()
    assert [f["type"] for f in frames if f["type"] in ("tool-call", "approval-required")] == []
    assert not (ws / "studies" / "m1").exists()


def test_ask_mode_offers_only_read_operations_and_refuses_changes(env):
    client, ws, app = env
    frames = _turn(client, prompt="create a study", mode="ask")
    assert set(SEEN["tools"][0]) == {"list_operations", "describe_operation", "call_operation", "wait_seconds"}
    res = next(f for f in frames if f["type"] == "tool-result")["content"]
    assert "read-only" in res["error"].lower()
    assert "approval-required" not in [f["type"] for f in frames]     # refused outright, not asked
    assert not (ws / "studies" / "m1").exists() and not ai_tools.audit_path(ws).exists()

    async def go():
        d = ai_tools.ChatDeps(app=app, client=ai_tools.make_client(app), ws_root=ws, session_key="t",
                              provider="p", model="m", mode="ask")
        ctx = RunContext(deps=d, model=TestModel(), usage=RunUsage())
        try:
            return await ai_tools.list_operations(ctx)
        finally:
            await d.client.aclose()
    listed = asyncio.run(go())
    assert listed["operations"] and not any(o["mutating"] for o in listed["operations"])
    assert listed["total"] == ai_tools.capabilities(app)["reads"]


def test_agent_mode_still_pauses_for_approval(env):
    client, ws, _ = env
    frames = _turn(client, prompt="create a study", mode="agent")
    assert [f["type"] for f in frames][-2:] == ["approval-required", "done"]
    assert not (ws / "studies" / "m1").exists()


def test_default_mode_is_agent_for_api_clients_and_bad_modes_are_422(env):
    client, _, _ = env
    assert "approval-required" in [f["type"] for f in _turn(client, prompt="create a study")]
    r = client.post("/api/chat/turn", json={"messages": [], "prompt": "hi", "mode": "yolo"}, headers=H)
    assert r.status_code == 422


def test_workspace_summary_can_be_switched_off(env):
    client, _, _ = env
    _turn(client, prompt="hello", include_manifest=True)
    _turn(client, prompt="hello", include_manifest=False)
    with_summary, without = SEEN["instructions"][0], SEEN["instructions"][1]
    assert '"name":"modes"' in with_summary
    assert '"name":"modes"' not in without and len(without) < len(with_summary)


def test_reasoning_streams_as_its_own_frames(env):
    client, _, _ = env
    frames = _turn(client, prompt="think about it", mode="manual")
    types = [f["type"] for f in frames]
    assert "reasoning-delta" in types
    assert "".join(f["text"] for f in frames if f["type"] == "reasoning-delta") == "pondering deeply"
    assert "".join(f["text"] for f in frames if f["type"] == "text-delta") == "answer"
    assert types.index("reasoning-delta") < types.index("text-delta")


def test_mentions_are_explained_to_the_model(env):
    client, _, _ = env
    _turn(client, prompt="hello")
    assert "@study/" in SEEN["instructions"][0]


def test_capabilities_endpoint_counts_the_real_surface(env):
    client, _, app = env
    r = client.get("/api/ai/capabilities", headers=H)
    assert r.status_code == 200
    body = r.json()
    idx = ai_tools.get_index(app)
    assert body["reads"] == sum(1 for e in idx.values() if not e["mutating"])
    assert body["writes"] == sum(1 for e in idx.values() if e["mutating"])
    assert body["reads"] > 50 and body["writes"] > 50
    assert any("/api/workspaces" in x for x in body["excluded"])

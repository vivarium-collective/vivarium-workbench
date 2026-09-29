"""The chat turn end to end (lib/ai_chat.py, POST /api/chat/turn).

Stubbed: ONLY the remote LLM — pydantic-ai's ``FunctionModel`` scripted by the
test, injected where the provider model is built. Real: the FastAPI app and
middleware, the live OpenAPI, the tools, the approval pause/resume, the
transcript round-trip through ``ModelMessagesTypeAdapter``, the audit log, the
filesystem. Provider acceptance of a real key/model is NOT proven here.
"""
import json
import sys

import pytest

pytest.importorskip("pydantic_ai")
from fastapi.testclient import TestClient  # noqa: E402
from pydantic_ai.exceptions import ModelHTTPError  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_auth, ai_tools  # noqa: E402

KEY = "sk-ant-CHATSECRET-abcdefghijklmnopqrstuv"
H = {"X-VW-Session": "tab-1"}
SEEN = {"instructions": []}


def _oid(app, method, path):
    return app.openapi()["paths"][path][method]["operationId"]


def _last_user_prompt(messages):
    for m in reversed(messages):
        for p in getattr(m, "parts", []):
            if isinstance(p, UserPromptPart):
                return str(p.content)
    return ""


def _decide(app, messages):
    """The scripted 'LLM': ('tool', args) or ('text', str)."""
    last = messages[-1]
    if any(isinstance(p, ToolReturnPart) for p in getattr(last, "parts", [])):
        denied = any(getattr(p, "outcome", None) == "denied" for p in last.parts)
        return ("text", "Understood, I will not do that." if denied else "Done.")
    prompt = _last_user_prompt(messages)
    if "list" in prompt:
        return ("tool", {"operation_id": _oid(app, "get", "/api/investigations")})
    if "boom" in prompt:
        raise ModelHTTPError(401, "fm", {"error": f"bad key {KEY}"})
    if "forge" in prompt:
        return ("tool", {"operation_id": _oid(app, "post", "/api/source/switch"), "body": {}})
    return ("tool", {"operation_id": _oid(app, "post", "/api/study-create"),
                     "body": {"name": "chat-made"}})


def _fake_llm(app):
    def function(messages, info):
        SEEN["instructions"].append(info.instructions)
        kind, val = _decide(app, messages)
        if kind == "text":
            return ModelResponse(parts=[TextPart(val)])
        return ModelResponse(parts=[ToolCallPart("call_operation", val)])

    async def stream(messages, info):
        SEEN["instructions"].append(info.instructions)
        kind, val = _decide(app, messages)
        if kind == "text":
            yield val[:5]
            yield val[5:]
        else:
            yield {0: DeltaToolCall(name="call_operation", json_args=json.dumps(val),
                                    tool_call_id="call-1")}

    return FunctionModel(function, stream_function=stream, model_name="fm")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: chat-test\n")
    (ws / ".pbg").mkdir()
    app = appmod.create_app()
    app.state.bind_host = "0.0.0.0"            # hosted mode: memory-only, no keyring/disk
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    ai_auth._MEMORY.clear()
    ai_auth._SELECTION.clear()
    SEEN["instructions"].clear()
    ai_auth.save_credential("anthropic", KEY, None, mode="memory", session="tab-1")
    ai_auth.set_selection("anthropic", "fm", mode="memory", session="tab-1")
    monkeypatch.setattr(ai_auth, "build_model", lambda provider, model, cred: _fake_llm(app))
    yield TestClient(app), ws, app
    ai_auth._MEMORY.clear()
    ai_auth._SELECTION.clear()


def _turn(client, **body):
    body.setdefault("messages", [])
    r = client.post("/api/chat/turn", json=body, headers=H)
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines()]


def _types(frames):
    return [f["type"] for f in frames]


def test_read_turn_streams_tool_call_result_text_and_done(env):
    client, ws, _ = env
    frames = _turn(client, prompt="please list the studies")
    t = _types(frames)
    assert t[0] == "tool-call" and t[1] == "tool-result" and t[-1] == "done"
    assert set(t[2:-1]) == {"text-delta"}
    assert "".join(f["text"] for f in frames if f["type"] == "text-delta") == "Done."
    assert frames[1]["ok"] is True and frames[1]["content"]["status"] == 200
    done = frames[-1]
    assert done["pending_approval"] is False
    # the transcript the browser will keep round-trips through the real adapter
    hist = ModelMessagesTypeAdapter.validate_python(done["messages"])
    assert any(isinstance(p, UserPromptPart) for m in hist for p in getattr(m, "parts", []))
    assert not (ws / "studies" / "chat-made").exists()


def test_system_instructions_carry_the_live_manifest(env):
    client, _, _ = env
    _turn(client, prompt="please list")
    ins = SEEN["instructions"][0]
    assert "call_operation" in ins and '"name":"chat-test"' in ins


def test_mutation_pauses_then_approve_executes_and_audits(env):
    client, ws, _ = env
    f1 = _turn(client, prompt="create a study")
    assert _types(f1) == ["tool-call", "approval-required", "done"]
    ask = f1[1]
    assert ask["metadata"]["method"] == "POST" and ask["metadata"]["path"] == "/api/study-create"
    assert ask["metadata"]["body"] == {"name": "chat-made"}
    assert f1[2]["pending_approval"] is True
    assert not (ws / "studies" / "chat-made").exists()       # paused, nothing written
    assert not ai_tools.audit_path(ws).exists()

    f2 = _turn(client, messages=f1[2]["messages"],
               deferred_results={"approvals": {ask["tool_call_id"]: True}})
    assert "tool-result" in _types(f2) and _types(f2)[-1] == "done"
    assert next(f for f in f2 if f["type"] == "tool-result")["content"]["status"] == 200
    assert f2[-1]["pending_approval"] is False
    assert (ws / "studies" / "chat-made" / "study.yaml").is_file()
    rec = json.loads(ai_tools.audit_path(ws).read_text())
    assert (rec["provider"], rec["model"], rec["method"], rec["path"], rec["status"], rec["approved"]) == (
        "anthropic", "fm", "POST", "/api/study-create", 200, True)


def test_deny_leaves_the_tree_unchanged(env):
    client, ws, _ = env
    f1 = _turn(client, prompt="create a study")
    cid = f1[1]["tool_call_id"]
    f2 = _turn(client, messages=f1[2]["messages"],
               deferred_results={"approvals": {cid: {"denied": "not now"}}})
    assert _types(f2)[-1] == "done" and f2[-1]["pending_approval"] is False
    assert "".join(f["text"] for f in f2 if f["type"] == "text-delta") == "Understood, I will not do that."
    assert not (ws / "studies" / "chat-made").exists()
    assert not ai_tools.audit_path(ws).exists()


def test_tampered_transcript_cannot_reach_an_excluded_operation(env):
    client, ws, app = env
    f1 = _turn(client, prompt="create a study")
    cid = f1[1]["tool_call_id"]
    forged = _oid(app, "post", "/api/source/switch")
    msgs = json.loads(json.dumps(f1[2]["messages"]))
    for m in msgs:                                    # the browser edits the pending call
        for p in m.get("parts", []):
            if p.get("part_kind") == "tool-call":
                p["args"] = {"operation_id": forged, "body": {}}
    f2 = _turn(client, messages=msgs, deferred_results={"approvals": {cid: True}})
    res = next(f for f in f2 if f["type"] == "tool-result")
    assert "unknown operation" in res["content"]["error"]
    assert not ai_tools.audit_path(ws).exists()


def test_unknown_approval_id_is_an_error_frame_not_a_crash(env):
    client, ws, _ = env
    f1 = _turn(client, prompt="create a study")
    f2 = _turn(client, messages=f1[2]["messages"], deferred_results={"approvals": {"bogus": True}})
    assert _types(f2) == ["error"]
    assert not (ws / "studies" / "chat-made").exists()


def test_provider_error_mid_turn_is_an_error_frame_with_the_key_masked(env):
    client, _, _ = env
    frames = _turn(client, prompt="boom")
    assert _types(frames) == ["error"]
    assert KEY not in frames[0]["error"] and "<redacted>" in frames[0]["error"]


def test_stream_opts_out_of_gzip_so_chunks_are_not_buffered(env):
    client, _, _ = env
    r = client.post("/api/chat/turn", json={"messages": [], "prompt": "list"},
                    headers={**H, "Accept-Encoding": "gzip"})
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert r.headers["content-encoding"] == "identity"


# --- preflight errors are plain JSON envelopes, before any streaming --------


def test_preflight_errors(env, monkeypatch):
    client, _, _ = env

    def post(**body):
        body.setdefault("messages", [])
        return client.post("/api/chat/turn", json=body, headers=H)

    assert post().status_code == 422                                   # neither prompt nor deferred
    assert post(prompt="a", deferred_results={"approvals": {"x": True}}).status_code == 422
    assert post(prompt="   ").status_code == 422
    assert post(prompt="hi", messages=[{"nonsense": 1}]).status_code == 422
    assert post(deferred_results={"approvals": {}}).status_code == 422
    assert post(deferred_results={"approvals": {"x": "maybe"}}).status_code == 422
    # no credentials / no selection for this session
    r = client.post("/api/chat/turn", json={"messages": [], "prompt": "hi"},
                    headers={"X-VW-Session": "someone-else"})
    assert r.status_code == 409 and "no AI provider" in r.json()["error"]
    ai_auth.delete_credential("anthropic", mode="memory", session="tab-1")
    r = post(prompt="hi")
    assert r.status_code == 409 and "no credentials" in r.json()["error"]
    monkeypatch.setitem(sys.modules, "pydantic_ai", None)
    r = post(prompt="hi")
    assert r.status_code == 503 and r.json()["error"] == ai_auth.INSTALL_HINT


def test_chat_routes_are_not_offered_to_the_model(env):
    _, _, app = env
    paths = {e["path"] for e in ai_tools.build_index(app).values()}
    assert "/api/chat/turn" not in paths and not any(p.startswith("/api/ai") for p in paths)


# --- contract: REAL server frames -> REAL client reducer (chat-core.js) ------

import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

_NODE = shutil.which("node")
_CONTRACT = Path(__file__).parent / "js" / "contract_chat_core.js"


def _reduce(tmp_path, ndjson, chunk, prior=None):
    f = tmp_path / "frames.ndjson"
    f.write_text(ndjson)
    args = [_NODE, str(_CONTRACT), str(f), str(chunk)]
    if prior is not None:
        p = tmp_path / "prior.json"
        p.write_text(json.dumps(prior))
        args.append(str(p))
    out = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(_NODE is None, reason="node not installed")
@pytest.mark.parametrize("chunk", [7, 64, 100000])
def test_client_reducer_consumes_real_frames_through_approve(env, tmp_path, chunk):
    client, ws, _ = env
    r1 = client.post("/api/chat/turn", json={"messages": [], "prompt": "create a study"}, headers=H)
    s1 = _reduce(tmp_path, r1.text, chunk)
    tool = s1["ui"][1]["parts"][0]
    assert (tool["kind"], tool["status"]) == ("tool", "awaiting")
    assert tool["approval"]["method"] == "POST" and tool["approval"]["body"] == {"name": "chat-made"}
    assert s1["pending"] == [tool["id"]] and s1["transcript"]
    # the transcript the JS kept is exactly what the server accepts back
    s1["ui"][1]["parts"][0]["status"] = "running"
    body = {"messages": s1["transcript"], "deferred_results": {"approvals": {tool["id"]: True}}}
    r2 = client.post("/api/chat/turn", json=body, headers=H)
    s2 = _reduce(tmp_path, r2.text, chunk, prior={**s1, "pending": []})
    parts = s2["ui"][1]["parts"]
    assert parts[0]["status"] == "done" and parts[0]["result"]["status"] == 200
    assert parts[-1] == {"kind": "text", "text": "Done."}
    assert (ws / "studies" / "chat-made" / "study.yaml").is_file()

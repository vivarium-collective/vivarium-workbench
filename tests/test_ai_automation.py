"""Can the chat really automate the workbench? (everything a user does EXCEPT pushing commits)

The end-to-end test drives create-study -> baseline -> variant -> run -> poll -> read results
ONLY through ``ai_tools.call_operation`` on the real app, with a real detached run subprocess
on a copy of the ws_increase_demo fixture. It proves the *tool layer*; model planning needs a
real LLM (see the live probe). Documented product gaps are pinned with strict xfails so a fix
flips them loudly.
"""
import asyncio
import contextlib
import json
import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("pydantic_ai")
from pydantic_ai import RunContext  # noqa: E402
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart  # noqa: E402
from pydantic_ai.models.function import DeltaToolCall, FunctionModel  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_auth, ai_chat, ai_tools  # noqa: E402

REPO = Path(__file__).parent.parent
FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
COMPOSITE = "pbg_ws_increase_demo.composites.increase-demo"


def _oid(app, method, path):
    return app.openapi()["paths"][path][method]["operationId"]


def _make_app(tmp_path, monkeypatch=None, *, fixture=True):
    ws = tmp_path / "ws"
    if fixture:
        shutil.copytree(FIXTURE, ws)
    else:
        ws.mkdir()
        (ws / "workspace.yaml").write_text("name: auto\n")
        (ws / ".pbg").mkdir()
    app = appmod.create_app()
    app.state.bind_host = "127.0.0.1"
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    if monkeypatch is not None:                      # the detached child needs the repo + the workspace package
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(REPO), str(ws), os.environ.get("PYTHONPATH", "")]))
    return app, ws


@contextlib.asynccontextmanager
async def _session(app, ws):
    d = ai_tools.ChatDeps(app=app, client=ai_tools.make_client(app), ws_root=ws, session_key="tab-1",
                          provider="p", model="m")
    n = {"i": 0}

    def ctx(approved=False):
        n["i"] += 1
        return RunContext(deps=d, model=TestModel(), usage=RunUsage(), tool_call_approved=approved,
                          tool_call_id=f"call-{n['i']}")

    async def call(oid, *, approved=None, **kw):
        # like the chat: a write first pauses for approval, then runs once approved
        idx = ai_tools.get_index(app)[oid]
        if idx["mutating"] and approved is None:
            approved = True
        return await ai_tools.call_operation(ctx(bool(approved)), oid, **kw)

    try:
        yield d, ctx, call
    finally:
        await d.client.aclose()


# --- exclusions: everything a user does, except pushing ----------------------------------


def test_registry_installs_are_now_reachable_but_pushing_and_rebinding_are_not(tmp_path):
    app, _ = _make_app(tmp_path, fixture=False)
    served = {(e["method"], e["path"]) for e in ai_tools.get_index(app).values()}
    for path in ("/api/catalog-install", "/api/catalog-uninstall", "/api/import-install",
                 "/api/system-deps-install"):
        assert ("POST", path) in served, f"{path} should be reachable (approval-gated)"
    for path in ("/api/branch/push", "/api/work-push", "/api/work-create-pr",     # the one exception: pushing
                 "/api/source/switch", "/api/workspaces/start", "/api/auth/github/start",
                 "/api/ai/credentials", "/api/chat/turn", "/api/events"):
        assert not any(p == path for _, p in served), f"{path} must stay unreachable"
    assert ("POST", "/api/dirty-commit-all") in served          # a LOCAL commit is fine


# --- select: page/slice big responses instead of a blind cut ------------------------------


def test_select_path_syntax():
    sel = ai_tools.select_path
    data = {"a": {"b": [{"c": 1}, {"c": 2}, {"c": 3}]}, "n": 5}
    assert sel(data, "a.b[1].c") == 2
    assert sel(data, "a.b[0:2]") == [{"c": 1}, {"c": 2}]
    assert sel(data, "a.b.2.c") == 3
    assert sel(data, "a.b[-1].c") == 3
    assert sel(data, "n") == 5 and sel(data, "") == data
    with pytest.raises(ValueError, match="keys: a, n"):
        sel(data, "missing")
    with pytest.raises(ValueError, match="list index"):
        sel(data, "a.b[9]")
    with pytest.raises(ValueError, match="not an object"):
        sel(data, "n.x")


def test_oversized_json_returns_a_shape_and_select_pages_it(tmp_path):
    app, ws = _make_app(tmp_path, fixture=True)
    oid = _oid(app, "get", "/api/registry")

    async def go():
        async with _session(app, ws) as (d, ctx, call):
            big = await call(oid)
            keys = big["shape"]
            first_list = next(k for k, v in keys.items() if v.startswith("list["))
            page = await call(oid, select=f"{first_list}[0:2]")
            bad = await call(oid, select="nope.nothing")
            return big, first_list, page, bad

    big, first_list, page, bad = asyncio.run(go())
    assert big["truncated"] is True and big["chars"] > ai_tools.MAX_RESPONSE_CHARS
    assert "select" in big["hint"] and len(json.dumps(big)) < 6000        # a summary, not a blind 20k cut
    assert page["status"] == 200 and isinstance(page["body"], list) and len(page["body"]) == 2
    assert "error" in bad and "keys:" in bad["error"]


# --- wait_seconds: a bounded sleep so polling isn't a hot loop -----------------------------


def test_wait_seconds_is_bounded(tmp_path, monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    app, ws = _make_app(tmp_path, fixture=False)
    monkeypatch.setattr(ai_tools.asyncio, "sleep", fake_sleep)

    async def go():
        async with _session(app, ws) as (d, ctx, call):
            return [await ai_tools.wait_seconds(ctx(), s) for s in (2, 999, -5)]

    out = asyncio.run(go())
    assert slept == [2, ai_tools.MAX_WAIT_S, 0]
    assert out[1]["waited"] == ai_tools.MAX_WAIT_S
    assert ai_tools.wait_seconds in ai_tools.TOOLS


# --- the prompt must tell the truth about the workbench ------------------------------------


def test_playbook_is_accurate_and_never_offers_push():
    agent = ai_chat.build_instructions("agent")
    assert "wait_seconds" in agent and "select" in agent
    assert "BLOCK" in agent and "study-run-baseline" in agent and "composite-test-run" in agent
    assert "WITHOUT `source`" in agent                               # study-create without a YAML source
    assert "never push" in agent.lower()
    assert "returns an id you must poll" not in agent               # the old, false claim
    ask = ai_chat.build_instructions("ask")
    assert "study-baseline-add" not in ask                          # write recipes only in Agent mode
    assert "no tools" in ai_chat.build_instructions("manual").lower()


def test_usage_limits_fit_a_multi_step_flow():
    assert ai_chat.USAGE_LIMITS.request_limit >= 100 and ai_chat.USAGE_LIMITS.tool_calls_limit >= 150


# --- keep-alive: a long tool call must not go silent on the stream --------------------------


def test_long_tool_calls_emit_keepalive_pings(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    app, ws = _make_app(tmp_path, fixture=False)
    ai_auth._MEMORY.clear(); ai_auth._SELECTION.clear()
    ai_auth.save_credential("anthropic", "sk-ant-abcdefghijklmnopqrstuvwx", None, mode="memory", session="t")
    ai_auth.set_selection("anthropic", "fm", mode="memory", session="t")
    app.state.bind_host = "0.0.0.0"
    monkeypatch.setattr(ai_chat, "KEEPALIVE_S", 0.15)

    def decide(messages):
        if any(isinstance(p, ToolReturnPart) for p in getattr(messages[-1], "parts", [])):
            return ("text", "done")
        return ("tool", {"seconds": 1})

    def function(messages, info):
        k, v = decide(messages)
        return ModelResponse(parts=[TextPart(v)] if k == "text" else [ToolCallPart("wait_seconds", v)])

    async def stream(messages, info):
        k, v = decide(messages)
        if k == "text":
            yield v
        else:
            yield {0: DeltaToolCall(name="wait_seconds", json_args=json.dumps(v), tool_call_id="w1")}

    monkeypatch.setattr(ai_auth, "build_model",
                        lambda *a, **k: FunctionModel(function, stream_function=stream, model_name="fm"))
    r = TestClient(app).post("/api/chat/turn", json={"messages": [], "prompt": "wait", "mode": "ask"},
                             headers={"X-VW-Session": "t"})
    frames = [json.loads(x) for x in r.text.splitlines()]
    types = [f["type"] for f in frames]
    assert types.count("ping") >= 2, types                     # silence would have dropped a proxy
    assert types[-1] == "done" and "error" not in types
    ai_auth._MEMORY.clear(); ai_auth._SELECTION.clear()


# --- the acceptance flow, only through call_operation, with a REAL run ---------------------


def test_create_baseline_variant_run_poll_and_read_results_through_the_chat_tools(tmp_path, monkeypatch):
    app, ws = _make_app(tmp_path, monkeypatch, fixture=True)
    op = lambda m, p: _oid(app, m, p)                                       # noqa: E731

    async def go():
        async with _session(app, ws) as (d, ctx, call):
            out = {}
            out["create"] = await call(op("post", "/api/study-create"), body={"name": "auto-1"})
            out["baseline"] = await call(op("post", "/api/study-baseline-add"),
                                         body={"study": "auto-1", "name": "base", "composite": COMPOSITE})
            out["variant"] = await call(op("post", "/api/study-variant-add"),
                                        body={"study": "auto-1", "name": "fast", "base_composite": "base",
                                              "parameter_overrides": {"rate": 2.5}})
            out["run"] = await call(op("post", "/api/composite-test-run"),
                                    body={"id": COMPOSITE, "overrides": {"rate": 2.5}, "steps": 5})
            run_id = out["run"]["body"]["run_id"]
            status = None
            for _ in range(120):                                            # like the model: wait, then poll
                await ai_tools.wait_seconds(ctx(), 0.5)
                status = await call(op("get", "/api/composite-run/{run_id}/status"),
                                    path_params={"run_id": run_id})
                if status["body"].get("status") in ("completed", "failed", "orphaned", "cancelled"):
                    break
            out["status"] = status
            out["results"] = await call(op("get", "/api/composite-run/{run_id}"), path_params={"run_id": run_id})
            out["study"] = await call(op("get", "/api/study/{slug}"), path_params={"slug": "auto-1"})
            out["list"] = await call(op("get", "/api/investigations"))
            return out

    out = asyncio.run(go())
    assert out["create"]["status"] == 200 and (ws / "studies" / "auto-1").is_dir()
    assert out["baseline"]["status"] == 200, out["baseline"]
    assert out["variant"]["status"] == 200, out["variant"]
    assert out["run"]["status"] == 202 and out["run"]["body"]["status"] == "running"
    assert out["status"]["body"]["status"] == "completed", out["status"]
    assert out["results"]["status"] == 200
    assert out["study"]["status"] == 200
    assert any("auto-1" in json.dumps(v) for v in out["list"]["body"].values())
    # every change went through the approval-gated, audited path: intent then result per write
    recs = [json.loads(x) for x in ai_tools.audit_path(ws).read_text().splitlines()]
    writes = [r["operation_id"] for r in recs if r["phase"] == "result"]
    assert len(writes) == 4 and all(r["status"] in (200, 202) for r in recs if r["phase"] == "result")


# --- documented product gaps, pinned (fix them => these flip and force a doc/prompt update) ---


@pytest.mark.xfail(strict=True, reason="PRODUCT GAP: study-run-baseline resolves composites in-process and cannot "
                                       "find a YAML fixture composite (detached composite-test-run can)")
def test_pin_study_run_baseline_resolves_a_yaml_composite(tmp_path, monkeypatch):
    app, ws = _make_app(tmp_path, monkeypatch, fixture=True)

    async def go():
        async with _session(app, ws) as (d, ctx, call):
            await call(_oid(app, "post", "/api/study-create"), body={"name": "auto-2"})
            await call(_oid(app, "post", "/api/study-baseline-add"),
                       body={"study": "auto-2", "name": "base", "composite": COMPOSITE})
            return await call(_oid(app, "post", "/api/study-run-baseline"),
                              body={"study": "auto-2", "composite": "base", "steps": 3})

    assert asyncio.run(go())["status"] == 200


@pytest.mark.xfail(strict=True, reason="PRODUCT GAP: study-create with a YAML composite `source` writes a legacy "
                                       "v2 spec that a later baseline-add cannot extend (500)")
def test_pin_study_create_with_yaml_source_can_be_extended(tmp_path, monkeypatch):
    app, ws = _make_app(tmp_path, monkeypatch, fixture=True)

    async def go():
        async with _session(app, ws) as (d, ctx, call):
            await call(_oid(app, "post", "/api/study-create"), body={"name": "auto-3", "source": COMPOSITE})
            await call(_oid(app, "get", "/api/study/{slug}"), path_params={"slug": "auto-3"})
            return await call(_oid(app, "post", "/api/study-baseline-add"),
                              body={"study": "auto-3", "name": "b2", "composite": COMPOSITE})

    assert asyncio.run(go())["status"] == 200

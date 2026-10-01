"""The chat model's action surface (lib/ai_tools.py).

Real here: the FastAPI app (``create_app()`` with a ``get_workspace`` override on
a tmp workspace), its live ``openapi()``, the middleware stack, the in-process
ASGI client, the filesystem. Nothing about the thing under test is mocked; the
LLM is not involved (see test_ai_chat.py for the agent loop).
"""
import asyncio
import contextlib
import json

import httpx
import pytest

pytest.importorskip("pydantic_ai")
from fastapi.testclient import TestClient  # noqa: E402
from pydantic_ai import ApprovalRequired, RunContext  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_tools  # noqa: E402

# Operations that MUST be unreachable. The first test also asserts each really
# exists in the live schema, so the exclusion check can't pass vacuously.
MUST_EXCLUDE = [
    ("post", "/api/source/switch"), ("post", "/api/source/materialize-repo"),
    ("post", "/api/workspaces/start"), ("post", "/api/workspaces/stop"),
    ("post", "/api/workspaces/add"), ("post", "/api/branch/push"),
    ("post", "/api/work-push"), ("get", "/api/events"), ("get", "/api/events/log"),
    ("post", "/api/auth/github/start"), ("post", "/api/ai/credentials"),
]


def _make_app(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: chat-test\n")
    (ws / ".pbg").mkdir()
    app = appmod.create_app()
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    return app, ws


def _oid(app, method, path):
    return app.openapi()["paths"][path][method]["operationId"]


@contextlib.asynccontextmanager
async def _deps(app, ws, client=None):
    d = ai_tools.ChatDeps(app=app, client=client or ai_tools.make_client(app), ws_root=ws,
                          session_key="tab-1", provider="test", model="m")
    try:
        yield d
    finally:
        await d.client.aclose()


def _ctx(deps, approved=False):
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage(), tool_call_approved=approved)


def _run(coro):
    return asyncio.run(coro)


def test_index_is_built_from_the_live_schema_and_excludes_the_dangerous(tmp_path):
    app, _ = _make_app(tmp_path)
    spec = app.openapi()["paths"]
    index = ai_tools.build_index(app)
    served = {(e["method"].lower(), e["path"]) for e in index.values()}
    for method, path in MUST_EXCLUDE:
        assert method in spec[path], f"{method} {path} no longer exists — update MUST_EXCLUDE"
        assert (method, path) not in served
    assert not any(p.startswith(ai_tools.EXCLUDED_PATH_PREFIXES) for _, p in served)
    assert not any(e["tag"] in ai_tools.EXCLUDED_TAGS for e in index.values())
    # ...but the ordinary surface is there, from the same source
    assert ("post", "/api/study-create") in served and ("get", "/api/investigations") in served
    assert len(index) > 100


def test_real_get_matches_testclient(tmp_path):
    app, ws = _make_app(tmp_path)
    oid = _oid(app, "get", "/api/investigations")

    async def go():
        async with _deps(app, ws) as d:
            return await ai_tools.call_operation(_ctx(d), oid)

    out = _run(go())
    direct = TestClient(app).get("/api/investigations")
    assert out["status"] == direct.status_code == 200
    assert out["body"] == direct.json()


def test_post_pauses_for_approval_and_does_not_execute(tmp_path):
    app, ws = _make_app(tmp_path)
    oid = _oid(app, "post", "/api/study-create")

    async def go():
        async with _deps(app, ws) as d:
            await ai_tools.call_operation(_ctx(d), oid, body={"name": "chat-made"})

    with pytest.raises(ApprovalRequired) as e:
        _run(go())
    assert e.value.metadata["method"] == "POST"
    assert e.value.metadata["path"] == "/api/study-create"
    assert e.value.metadata["body"] == {"name": "chat-made"}
    assert not (ws / "studies" / "chat-made").exists()
    assert not ai_tools.audit_path(ws).exists()


def test_approved_post_executes_and_is_audited(tmp_path):
    app, ws = _make_app(tmp_path)
    oid = _oid(app, "post", "/api/study-create")

    async def go():
        async with _deps(app, ws) as d:
            return await ai_tools.call_operation(_ctx(d, approved=True), oid,
                                                 body={"name": "chat-made"})

    out = _run(go())
    assert out == {"status": 200, "body": {"ok": True, "name": "chat-made"}}
    assert (ws / "studies" / "chat-made" / "study.yaml").is_file()
    recs = [json.loads(x) for x in ai_tools.audit_path(ws).read_text().splitlines()]
    assert [r["phase"] for r in recs] == ["intent", "result"]      # intent BEFORE dispatch, then the result
    intent, rec = recs
    assert intent["approved"] is True and "status" not in intent
    assert {k: rec[k] for k in ("provider", "model", "operation_id", "method", "path",
                                "status", "approved", "outcome")} == {
        "provider": "test", "model": "m", "operation_id": oid,
        "method": "POST", "path": "/api/study-create", "status": 200, "approved": True,
        "outcome": "completed"}
    assert rec["session"] == ai_tools.session_tag("tab-1") and "tab-1" not in json.dumps(rec)
    assert rec["ts"].endswith("+00:00")


def test_failed_mutation_is_still_audited_with_its_status(tmp_path):
    app, ws = _make_app(tmp_path)
    oid = _oid(app, "post", "/api/study-create")

    async def go():
        async with _deps(app, ws) as d:
            return await ai_tools.call_operation(_ctx(d, approved=True), oid,
                                                 body={"name": "bad name!"})

    out = _run(go())
    assert out["status"] == 400
    last = json.loads(ai_tools.audit_path(ws).read_text().splitlines()[-1])
    assert last["phase"] == "result" and last["status"] == 400


@pytest.mark.parametrize("method,path", MUST_EXCLUDE)
def test_forged_excluded_operation_id_is_refused_even_when_approved(tmp_path, method, path):
    app, ws = _make_app(tmp_path)
    forged = _oid(app, method, path)      # a real id the model could have made up / a tampered transcript

    async def go():
        async with _deps(app, ws) as d:
            return await ai_tools.call_operation(_ctx(d, approved=True), forged)

    out = _run(go())
    assert "error" in out and "status" not in out
    assert not ai_tools.audit_path(ws).exists()


def test_unknown_operation_and_missing_path_param_are_reported_not_raised(tmp_path):
    app, ws = _make_app(tmp_path)
    templated = next(e for e in ai_tools.get_index(app).values()
                     if e["method"] == "GET" and "{" in e["path"])

    async def go():
        async with _deps(app, ws) as d:
            return (await ai_tools.call_operation(_ctx(d), "nope"),
                    await ai_tools.call_operation(_ctx(d), templated["operation_id"]))

    unknown, missing = _run(go())
    assert "unknown operation" in unknown["error"]
    assert "missing path parameter" in missing["error"]


def test_path_params_are_url_quoted():
    assert ai_tools._resolve_path("/api/x/{slug}", {"slug": "a/b c"}) == "/api/x/a%2Fb%20c"
    assert ai_tools._resolve_path("/api/x/{rel:path}", {"rel": "a/b c"}) == "/api/x/a/b%20c"


def test_readonly_mode_drops_mutating_operations(tmp_path, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_READONLY", "1")
    app = appmod.create_app()
    index = ai_tools.build_index(app)
    assert "/api/study-create" not in {e["path"] for e in index.values()}
    allowed = appmod._READONLY_ALLOWED_MUTATIONS
    assert all(e["path"] in allowed for e in index.values() if e["mutating"])


def test_list_and_describe_operations(tmp_path):
    app, ws = _make_app(tmp_path)
    oid = _oid(app, "post", "/api/study-create")

    async def go():
        async with _deps(app, ws) as d:
            ctx = _ctx(d)
            return (await ai_tools.list_operations(ctx, query="study create"),
                    await ai_tools.list_operations(ctx),
                    await ai_tools.list_operations(ctx, tag="Studies", query="zzz-nothing"),
                    await ai_tools.describe_operation(ctx, oid),
                    await ai_tools.describe_operation(ctx, "nope"))

    narrowed, everything, none, desc, bad = _run(go())
    assert any(o["path"] == "/api/study-create" for o in narrowed["operations"])
    assert everything["truncated"] is True and len(everything["operations"]) == ai_tools.MAX_LIST_RESULTS
    assert none == {"total": 0, "truncated": False, "operations": []}
    assert desc["mutating"] is True and "name" in desc["request_body"]["properties"]
    assert "$ref" not in json.dumps(desc["request_body"])
    assert "unknown operation" in bad["error"]


def test_response_shaping_truncates_and_summarises_non_json():
    big = httpx.Response(200, json={"a": "x" * (ai_tools.MAX_RESPONSE_CHARS + 10)})
    out = ai_tools._shape(big)
    assert out["truncated"] is True and out["shape"] == {"a": "string"} and len(out["body_preview"]) == 2000
    assert "select" in out["hint"]
    html = ai_tools._shape(httpx.Response(200, text="<p>hi</p>", headers={"content-type": "text/html"}))
    assert html == {"status": 200, "content_type": "text/html", "bytes": 9, "text_preview": "<p>hi</p>"}
    blob = ai_tools._shape(httpx.Response(200, content=b"\x00\x01", headers={"content-type": "application/zip"}))
    assert "text_preview" not in blob and blob["bytes"] == 2


def test_session_header_is_forwarded_and_no_origin_is_sent(tmp_path):
    app, ws = _make_app(tmp_path)
    oid = _oid(app, "get", "/api/investigations")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    async def go():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x")
        async with _deps(app, ws, client) as d:
            await ai_tools.call_operation(_ctx(d), oid)

    _run(go())
    assert seen["x-vw-session"] == "tab-1" and "origin" not in seen


# -- object arguments: typed as objects, and a JSON object sent as a string is decoded (local models do this) --


def _call_operation_tool():
    from pydantic_ai.tools import Tool
    return Tool(ai_tools.call_operation)


def test_call_operation_tells_the_model_its_object_arguments_are_objects():
    props = _call_operation_tool().tool_def.parameters_json_schema["properties"]
    for name in ("path_params", "query", "body"):
        kinds = {s.get("type") for s in props[name]["anyOf"]}
        assert kinds == {"object", "null"}, (name, props[name])


def test_stringified_object_arguments_are_decoded_by_the_tool_validator():
    v = _call_operation_tool().function_schema.validator    # the validation pydantic-ai runs on every tool call
    args = v.validate_python({"operation_id": "x", "body": '{"study": "repeat-matching"}',
                              "query": ' {"slug": "s1"}', "path_params": '{"run_id": "r1"}'})
    assert args["body"] == {"study": "repeat-matching"}
    assert args["query"] == {"slug": "s1"} and args["path_params"] == {"run_id": "r1"}


@pytest.mark.parametrize("bad", ["repeat-matching", "{not json", '["a", "b"]', "42"])
def test_text_that_is_not_a_json_object_is_rejected_not_sent(bad):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _call_operation_tool().function_schema.validator.validate_python({"operation_id": "x", "body": bad})

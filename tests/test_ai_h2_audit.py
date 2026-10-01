"""S-07: the assistant's audit log says what was asked, not only that something was.

Real here: the FastAPI app, the in-process client, the tools and the audit file. The LLM is not involved.
"""
import asyncio
import contextlib
import hashlib
import json

import pytest

pytest.importorskip("pydantic_ai")
from pydantic_ai import RunContext  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_tools  # noqa: E402


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: audit\n")
    app = appmod.create_app()
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    return app, ws


def _oid(app, method, path):
    return app.openapi()["paths"][path][method]["operationId"]


def _call(app, ws, oid, *, approved=False, call_id="call-1", **kw):
    async def go():
        d = ai_tools.ChatDeps(app=app, client=ai_tools.make_client(app), ws_root=ws, session_key="tab-1",
                              provider="test", model="m")
        ctx = RunContext(deps=d, model=TestModel(), usage=RunUsage(), tool_call_approved=approved,
                         tool_call_id=call_id)
        try:
            return await ai_tools.call_operation(ctx, oid, **kw)
        finally:
            await d.client.aclose()
    return asyncio.run(go())


def _records(ws):
    return [json.loads(x) for x in ai_tools.audit_path(ws).read_text().splitlines()]


def test_the_intent_line_records_a_redacted_capped_preview_and_a_hash_of_the_arguments(env):
    app, ws = env
    secret = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"
    body = {"name": "audited", "overview": f"key {secret} " + "x" * 50_000}
    _call(app, ws, _oid(app, "post", "/api/study-create"), approved=True, body=body)
    intent = next(r for r in _records(ws) if r["phase"] == "intent")
    preview = json.dumps(intent["args"])
    assert "audited" in preview                                   # says what was asked
    assert secret not in preview and "<redacted>" in preview      # a key-shaped token never reaches the log
    assert len(preview) < 6_000                                   # size-capped, not the 50 kB body
    canonical = json.dumps({"query": {}, "body": body}, sort_keys=True, separators=(",", ":"), default=str)
    assert intent["args_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()


def test_reads_are_recorded_by_path_only(env):
    app, ws = env
    _call(app, ws, _oid(app, "get", "/api/study-charts/{slug}"), path_params={"slug": "s1"},
          query={"token": "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"})
    [rec] = [r for r in _records(ws) if r["phase"] == "read"]
    assert rec["path"] == "/api/study-charts/s1" and rec["method"] == "GET"
    assert "query" not in rec and "args" not in rec and "sk-ant" not in json.dumps(rec)


def test_a_failing_read_record_does_not_fail_the_read(env, monkeypatch):
    app, ws = env

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(ai_tools, "append_audit", boom)
    out = _call(app, ws, _oid(app, "get", "/api/study-charts/{slug}"), path_params={"slug": "s1"})
    assert "error" not in out or "audit" not in str(out.get("error", ""))


def test_claim_lookup_reads_only_what_was_appended_since_the_last_one(env, monkeypatch):
    _app, ws = env
    path = ai_tools.audit_path(ws)
    path.write_text("".join(json.dumps({"phase": "read", "path": f"/p{i}"}) + "\n" for i in range(2000)))
    ai_tools.append_audit(ws, {"phase": "intent", "tool_call_id": "a", "digest": "d1"})

    seen = []
    real = ai_tools._read_from

    def spy(p, offset):
        data = real(p, offset)
        seen.append(len(data))
        return data
    monkeypatch.setattr(ai_tools, "_read_from", spy)

    assert ai_tools._find_claim(ws, "a", "d1") == (True, None)
    first = seen[-1]
    ai_tools.append_audit(ws, {"phase": "result", "tool_call_id": "a", "digest": "d1", "status": 200})
    claimed, result = ai_tools._find_claim(ws, "a", "d1")
    assert claimed and result and result["status"] == 200         # sees the line appended after the first lookup
    assert seen[-1] < first / 20                                  # ...by reading only the new bytes
    assert ai_tools._find_claim(ws, "b", "d1") == (False, None)


def test_claim_lookup_survives_the_log_being_replaced(env):
    _app, ws = env
    ai_tools.append_audit(ws, {"phase": "intent", "tool_call_id": "a", "digest": "d1"})
    assert ai_tools._find_claim(ws, "a", "d1")[0] is True
    ai_tools.audit_path(ws).write_text("")                        # truncated/rotated
    assert ai_tools._find_claim(ws, "a", "d1") == (False, None)


def test_claim_lookup_notices_a_different_file_even_when_it_is_larger(env):
    _app, ws = env
    path = ai_tools.audit_path(ws)
    ai_tools.append_audit(ws, {"phase": "intent", "tool_call_id": "a", "digest": "d1"})
    assert ai_tools._find_claim(ws, "a", "d1")[0] is True
    # e.g. a branch switch / restore replaces the log with another, bigger file that records a different claim
    path.unlink()
    with open(path, "w") as f:
        f.write(json.dumps({"phase": "intent", "tool_call_id": "z", "digest": "dz", "pad": "x" * 500}) + "\n")
    assert ai_tools._find_claim(ws, "z", "dz")[0] is True       # the new file's claim is seen (replay refused)
    assert ai_tools._find_claim(ws, "a", "d1") == (False, None)  # the old file's claim is gone


def test_a_record_after_a_crash_truncated_line_is_not_swallowed(env):
    _app, ws = env
    path = ai_tools.audit_path(ws)
    path.write_text('{"phase":"intent","tool_call_id":"half"')          # power cut mid-line: no trailing newline
    ai_tools.append_audit(ws, {"phase": "intent", "tool_call_id": "b", "digest": "d2"})
    assert ai_tools._find_claim(ws, "b", "d2")[0] is True

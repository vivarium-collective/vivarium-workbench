"""Limits on the chat for a shared server (lib/chat_limits.py, the credential store caps in lib/ai_auth.py).

Stubbed: ONLY the remote LLM (pydantic-ai's ``FunctionModel``). Real: the FastAPI app and middleware, the turn route's
slot claim/release, pydantic-ai's own ``total_tokens_limit`` enforcement and usage accounting, the credential store.
Token counts are checked against an independent oracle: the usage recorded on the model responses in the transcript the
turn hands back to the browser.
"""
import asyncio
import gc
import json

import pytest

pytest.importorskip("pydantic_ai")
from fastapi.testclient import TestClient  # noqa: E402
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_auth, chat_limits  # noqa: E402
from vivarium_workbench.lib.errors import APIError  # noqa: E402

H = {"X-VW-Session": "tab-1"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ("CHAT_MAX_TURNS", "CHAT_SESSION_TOKENS", "CHAT_STORE_MAX", "CHAT_STORE_TTL_S"):
        monkeypatch.delenv(f"VIVARIUM_WORKBENCH_{k}", raising=False)
        monkeypatch.delenv(f"VIVARIUM_DASHBOARD_{k}", raising=False)
    chat_limits._ACTIVE.clear()
    chat_limits._USED.clear()
    ai_auth._MEMORY.clear()
    ai_auth._SELECTION.clear()
    ai_auth._TOUCHED.clear()
    yield
    chat_limits._ACTIVE.clear()
    chat_limits._USED.clear()
    ai_auth._MEMORY.clear()
    ai_auth._SELECTION.clear()
    ai_auth._TOUCHED.clear()


# --- turn slots ---------------------------------------------------------------------------------------------------------


def test_defaults_apply_only_to_a_shared_server():
    assert chat_limits.max_turns(shared=True) == chat_limits.DEFAULT_TURNS
    assert chat_limits.session_token_limit(shared=True) == chat_limits.DEFAULT_SESSION_TOKENS
    assert chat_limits.max_turns(shared=False) == 0 and chat_limits.session_token_limit(shared=False) == 0


def test_the_environment_overrides_in_both_directions_and_a_bad_value_does_not_lift_the_limit(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "0")
    assert chat_limits.max_turns(shared=True) == 0                     # the operator switched it off
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "2")
    assert chat_limits.max_turns(shared=False) == 2                    # ...or switched it on for a private server
    for bad in ("lots", "-1", "-100"):          # unreadable or negative: the default stays, the limit is never lifted
        monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", bad)
        assert chat_limits.max_turns(shared=True) == chat_limits.DEFAULT_TURNS
        monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_SESSION_TOKENS", bad)
        assert chat_limits.session_token_limit(shared=True) == chat_limits.DEFAULT_SESSION_TOKENS
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_STORE_MAX", "-5")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_STORE_TTL_S", "-5")
    for i in range(ai_auth.MEMORY_MAX_SESSIONS + 3):
        ai_auth._MEMORY[(f"s{i}", "openai")] = ai_auth.Credential("k", None, "memory")
        ai_auth._TOUCHED[f"s{i}"] = ai_auth.time.monotonic()
    ai_auth._reap()
    assert len(ai_auth._TOUCHED) == ai_auth.MEMORY_MAX_SESSIONS         # a negative store limit did not switch the cap off


def test_a_turn_over_the_limit_is_refused_and_a_released_slot_is_reusable(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "2")
    a, b = chat_limits.claim_turn(True), chat_limits.claim_turn(True)
    with pytest.raises(APIError) as e:
        chat_limits.claim_turn(True)
    assert e.value.status_code == 429
    a()
    c = chat_limits.claim_turn(True)
    a()                                      # releasing twice must not free somebody else's slot
    with pytest.raises(APIError):
        chat_limits.claim_turn(True)
    b()
    c()
    assert not chat_limits._ACTIVE


def test_a_slot_that_was_never_released_expires(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "1")
    chat_limits.claim_turn(True)              # its stream was dropped before it started: nobody calls release
    with pytest.raises(APIError):
        chat_limits.claim_turn(True)
    monkeypatch.setattr(chat_limits, "LEASE_S", -1.0)
    chat_limits.claim_turn(True)              # the stale slot was reaped


# --- the stream wrapper ---------------------------------------------------------------------------------------------


async def _agen(items, boom=False):
    for i in items:
        yield i
    if boom:
        raise RuntimeError("provider failed")


def _drain(held):
    async def go():
        return [x async for x in held]
    return asyncio.run(go())


def test_a_stream_that_ends_gives_its_slot_back(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "1")
    held = chat_limits.Held(_agen(["a", "b"]), chat_limits.claim_turn(True))
    assert _drain(held) == ["a", "b"] and not chat_limits._ACTIVE


def test_a_stream_that_fails_gives_its_slot_back(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "1")
    held = chat_limits.Held(_agen(["a"], boom=True), chat_limits.claim_turn(True))
    with pytest.raises(RuntimeError):
        _drain(held)
    assert not chat_limits._ACTIVE


def test_a_stream_closed_early_gives_its_slot_back(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "1")

    async def go():
        held = chat_limits.Held(_agen(["a", "b", "c"]), chat_limits.claim_turn(True))
        await held.__anext__()
        await held.aclose()
    asyncio.run(go())
    assert not chat_limits._ACTIVE


def test_a_stream_dropped_before_it_starts_gives_its_slot_back(monkeypatch):
    """A client that disconnects before the first read: an unstarted async generator never runs its own `finally`."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "1")
    held = chat_limits.Held(_agen(["a"]), chat_limits.claim_turn(True))
    assert len(chat_limits._ACTIVE) == 1
    del held
    gc.collect()
    assert not chat_limits._ACTIVE


# --- token ledger -------------------------------------------------------------------------------------------------------


def test_the_token_ledger_is_per_session_and_refuses_a_spent_session(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_SESSION_TOKENS", "1000")
    assert chat_limits.tokens_left("a", True) == 1000
    chat_limits.spend("a", 600, True)
    assert chat_limits.tokens_left("a", True) == 400 and chat_limits.tokens_left("b", True) == 1000
    chat_limits.spend("a", 500, True)
    with pytest.raises(APIError) as e:
        chat_limits.tokens_left("a", True)
    assert e.value.status_code == 429


def test_nothing_is_recorded_and_nothing_refused_without_a_limit():
    chat_limits.spend("a", 10_000_000, False)
    assert chat_limits.tokens_left("a", False) is None and not chat_limits._USED


def test_the_ledger_forgets_the_least_recently_used_session(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_SESSION_TOKENS", "1000")
    monkeypatch.setattr(chat_limits, "MAX_SESSIONS", 2)
    for s in ("a", "b", "c"):
        chat_limits.spend(s, 1, True)
    assert list(chat_limits._USED) == ["b", "c"]


# --- the credential store -----------------------------------------------------------------------------------------------


def _save(session, key="sk-x-0000000000000000"):
    ai_auth.save_credential("openai", key, None, mode="memory", session=session)


def test_the_credential_store_keeps_only_the_most_recent_sessions(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_STORE_MAX", "3")
    for i in range(10):
        _save(f"s{i}")
        ai_auth.set_selection("openai", "m", mode="memory", session=f"s{i}")
    assert len(ai_auth._MEMORY) == 3 and len(ai_auth._SELECTION) == 3 and len(ai_auth._TOUCHED) == 3
    assert ai_auth.get_credential("openai", mode="memory", session="s0") is None      # forgotten, not just hidden
    assert ai_auth.get_selection(mode="memory", session="s0") is None
    assert ai_auth.get_credential("openai", mode="memory", session="s9") is not None


def test_a_session_in_use_outlives_newer_idle_ones(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_STORE_MAX", "2")
    _save("old")
    _save("mid")
    assert ai_auth.get_credential("openai", mode="memory", session="old") is not None     # reading marks it as used
    _save("new")
    assert ai_auth.get_credential("openai", mode="memory", session="old") is not None
    assert ai_auth.get_credential("openai", mode="memory", session="mid") is None


def test_an_idle_session_expires(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_STORE_TTL_S", "100")
    t = [1000.0]
    monkeypatch.setattr(ai_auth.time, "monotonic", lambda: t[0])
    _save("a")
    t[0] += 99
    assert ai_auth.get_credential("openai", mode="memory", session="a") is not None
    t[0] += 101
    assert ai_auth.get_credential("openai", mode="memory", session="a") is None
    assert not ai_auth._MEMORY


def test_the_single_user_scope_is_never_forgotten(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_STORE_MAX", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_STORE_TTL_S", "1")
    t = [1000.0]
    monkeypatch.setattr(ai_auth.time, "monotonic", lambda: t[0])
    _save(None)                                  # scope "": the one-user fallback, not a visitor
    _save("a")
    _save("b")
    t[0] += 10_000
    _save("c")
    assert ("", "openai") in ai_auth._MEMORY and ("a", "openai") not in ai_auth._MEMORY


# --- through the real turn route ----------------------------------------------------------------------------------------


def _llm():
    def function(messages, info):
        return ModelResponse(parts=[TextPart("ok " * 20)])

    async def stream(messages, info):
        yield "ok " * 10
        yield "ok " * 10
    return FunctionModel(function, stream_function=stream, model_name="fm")


@pytest.fixture
def route(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: chat-test\n")
    app = appmod.create_app()
    app.state.bind_host = "0.0.0.0"            # a shared server: credentials in memory, limits on by default
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    ai_auth._KR_CACHE.clear()
    ai_auth._KR_FAILED.clear()
    _save("tab-1")
    ai_auth.set_selection("openai", "fm", mode="memory", session="tab-1")
    monkeypatch.setattr(ai_auth, "build_model", lambda provider, model, cred: _llm())
    return TestClient(app)


def _post(client, **body):
    body.setdefault("messages", [])
    body.setdefault("mode", "manual")
    return client.post("/api/chat/turn", json=body, headers=H)


def _frames(r):
    return [f for f in (json.loads(x) for x in r.text.splitlines()) if f.get("type") != "ping"]


def _used_in(messages, skip=0):
    return sum(m.usage.total_tokens for m in ModelMessagesTypeAdapter.validate_python(messages)[skip:]
               if isinstance(m, ModelResponse))


def test_a_full_server_answers_429_before_streaming_and_recovers(route, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "2")
    held = [chat_limits.claim_turn(True), chat_limits.claim_turn(True)]
    r = _post(route, prompt="hello")
    assert r.status_code == 429 and "chat turns" in r.json()["error"]
    held[0]()
    r = _post(route, prompt="hello")
    assert r.status_code == 200 and _frames(r)[-1]["type"] == "done"


def test_a_finished_turn_releases_its_slot(route, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "1")
    for _ in range(3):                         # with one slot, three in a row only work if each turn gives it back
        assert _post(route, prompt="hello").status_code == 200
    assert not chat_limits._ACTIVE


def test_a_session_is_charged_what_the_transcript_says_it_used(route, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_SESSION_TOKENS", "1000000")
    done = _frames(_post(route, prompt="hello"))[-1]
    first = _used_in(done["messages"])
    assert first > 0 and chat_limits._USED["tab-1"] == first
    done2 = _frames(_post(route, prompt="and again", messages=done["messages"]))[-1]
    # the second turn is charged only for its own response, not for the history it carried
    assert chat_limits._USED["tab-1"] == first + _used_in(done2["messages"], skip=len(done["messages"]))


def test_a_spent_session_is_refused_the_next_turn(route, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_SESSION_TOKENS", "1000000")
    used = _used_in(_frames(_post(route, prompt="hello"))[-1]["messages"])
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_SESSION_TOKENS", str(used))
    r = _post(route, prompt="hello again")
    assert r.status_code == 429 and "token limit" in r.json()["error"]


def test_a_run_stopped_by_the_token_limit_is_still_charged(route, monkeypatch):
    """Without this, a run that hits the limit raises, is never counted, and the session could keep going forever."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_SESSION_TOKENS", "5")
    frames = _frames(_post(route, prompt="hello"))
    assert frames[-1]["type"] == "error" and "total_tokens_limit" in frames[-1]["error"]
    assert chat_limits._USED["tab-1"] > 0
    assert _post(route, prompt="hello again").status_code == 429


def test_a_failed_turn_does_not_keep_a_slot(route, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "1")
    r = route.post("/api/chat/turn", json={"messages": [], "mode": "manual", "prompt": "hi"}, headers={"X-VW-Session": "nobody"})
    assert r.status_code == 409                                       # no provider selected: refused after the slot was claimed
    assert not chat_limits._ACTIVE
    assert _post(route, prompt="hello").status_code == 200


def test_a_session_over_its_token_budget_does_not_keep_a_slot(route, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_SESSION_TOKENS", "10")
    chat_limits._USED["tab-1"] = 10
    assert _post(route, prompt="hello").status_code == 429
    assert not chat_limits._ACTIVE


@pytest.fixture
def private(tmp_path, monkeypatch):
    """A private loopback server: credentials in the config/keychain scope, not in memory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: chat-test\n")
    app = appmod.create_app()
    app.state.bind_host = "127.0.0.1"
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    ai_auth.configure_default(True)
    ai_auth.save_credential("ollama", None, "http://localhost:11434", mode="keyring", session=None)
    ai_auth.set_selection("ollama", "fm", mode="keyring", session=None)
    monkeypatch.setattr(ai_auth, "build_model", lambda provider, model, cred: _llm())
    yield TestClient(app, base_url="http://127.0.0.1")
    ai_auth.configure_default(False)


def test_a_private_server_keeps_no_slots_and_no_ledger_unless_the_operator_asks(private, monkeypatch):
    busy = [chat_limits.claim_turn(True) for _ in range(chat_limits.DEFAULT_TURNS)]     # a shared server's slots are all taken
    for _ in range(6):
        r = private.post("/api/chat/turn", json={"messages": [], "mode": "manual", "prompt": "hello"}, headers=H)
        assert r.status_code == 200 and _frames(r)[-1]["type"] == "done", r.text
    assert not chat_limits._USED and len(chat_limits._ACTIVE) == len(busy)             # it took no slot of its own
    for release in busy:
        release()
    # ...and the operator can switch the same limits on for it
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CHAT_MAX_TURNS", "1")
    held = chat_limits.claim_turn(False)
    r = private.post("/api/chat/turn", json={"messages": [], "mode": "manual", "prompt": "hello"}, headers=H)
    assert r.status_code == 429
    held()

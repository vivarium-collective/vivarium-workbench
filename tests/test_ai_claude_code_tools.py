"""Claude Code in Ask / Agent mode: the workbench's tools over MCP, and approvals that block inside the call.

A REAL workbench server (uvicorn, on a free port) serves the app and its MCP endpoint (lib/claude_mcp.py); a real
HTTP client drives ``POST /api/chat/turn``. Two kinds of `claude`:

* a small **stub** (runs everywhere, CI included). It speaks the real MCP protocol to the real endpoint and prints
  the stream events Claude Code prints, so the coordination this PR adds — approval blocking, pause / resume,
  parallel approvals, expiry, the token guard, transcript building — is exercised end to end without a model.
  The stub stands in for the CLI only; the server, the tools, the audit and the HTTP protocol are real.
* the **real** CLI (skipped when it is absent or signed out): proves a real model uses the tools, that a change
  waits for the user, and that Ask mode really cannot change anything.
"""
import json
import os
import shutil
import signal
import socket
import sys
import threading
import time

import httpx
import pytest

pytest.importorskip("pydantic_ai")
pytest.importorskip("mcp")
import uvicorn  # noqa: E402
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, ToolCallPart, ToolReturnPart  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_auth, ai_tools, chat_commands, claude_cli, claude_mcp, startup  # noqa: E402

CREATE = "study_create_api_study_create_post"
live = pytest.mark.skipif(not (shutil.which("claude") and claude_cli.logged_in()),
                          reason="needs the real `claude` CLI, signed in")


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


# --- a server, and a stub claude ----------------------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ws = tmp_path / "ws"
    (ws / ".pbg").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: cc-tools\n")
    app = appmod.create_app()
    app.state.bind_host = "127.0.0.1"
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    app.state.bind_port = port
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error",
                                        timeout_graceful_shutdown=startup.GRACEFUL_SHUTDOWN_S))   # what `serve` uses
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    app.state._test_server = (srv, thread)          # for the shutdown test
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    ai_auth.set_selection("claude-code", "haiku", mode="keyring", session=None)
    claude_cli.forget_login()
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120)
    yield client, ws, app
    client.close()
    claude_cli._IDLE.clear()                       # (the processes themselves are killed just below, by pid)
    for pid in list(claude_cli._LIVE_PIDS):
        claude_cli._signal_group(pid, signal.SIGKILL)
    claude_cli._LIVE_PIDS.clear()
    claude_mcp.BINDINGS.clear()
    srv.should_exit = True
    thread.join(10)
    ai_auth._SELECTION.clear()
    claude_cli.forget_login()


_STUB = r'''#!{py}
import json, os, sys, threading, time
import httpx
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True}})); sys.exit(0)
cfg_path = args[args.index("--mcp-config") + 1] if "--mcp-config" in args else None
cfg = json.load(open(cfg_path))["mcpServers"]["workbench"] if cfg_path else None
if os.environ.get("VW_STUB_OUT") and cfg_path:       # what the server really handed this process
    json.dump({{"argv": args, "cfg": open(cfg_path).read(), "cfg_mode": oct(os.stat(cfg_path).st_mode & 0o777),
               "timeouts": [os.environ.get("MCP_TOOL_TIMEOUT"), os.environ.get("CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT")]}},
              open(os.environ["VW_STUB_OUT"], "w"))
mode = os.environ.get("VW_STUB_MODE", "create")
lock = threading.Lock()
def emit(o):
    with lock:
        sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
def mcp(name, arguments, use_id):
    params = {{"name": name, "arguments": arguments}}
    if use_id:
        params["_meta"] = {{"claudecode/toolUseId": use_id}}
    r = httpx.post(cfg["url"], timeout=300, headers={{**cfg["headers"], "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}}, json={{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}})
    return r.json()["result"]
def tool_use(msg, use_id, name, inp):
    emit({{"type": "assistant", "message": {{"id": msg, "role": "assistant", "content": [
         {{"type": "tool_use", "id": use_id, "name": "mcp__workbench__" + name, "input": inp}}]}}}})
def tool_result(use_id, res):
    text = res["content"][0]["text"]
    emit({{"type": "user", "message": {{"role": "user", "content": [
         {{"type": "tool_result", "tool_use_id": use_id, "content": text, "is_error": bool(res.get("isError"))}}]}}}})
def finish(msg, text):
    emit({{"type": "stream_event", "event": {{"type": "message_start"}}}})
    emit({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": text}}}}}})
    emit({{"type": "assistant", "message": {{"id": msg, "role": "assistant", "content": [{{"type": "text", "text": text}}]}}}})
    emit({{"type": "result", "subtype": "success", "is_error": False, "result": text}})
def make(i, use_id):
    inp = {{"operation_id": "study_create_api_study_create_post", "body": {{"name": "stub-%d" % i}}}}
    tool_use("msg_1", use_id, "call_operation", inp)
    return inp
for line in sys.stdin:
    emit({{"type": "system", "subtype": "init", "tools": ["Skill"], "skills": [], "plugins": [],
          "mcp_servers": [{{"name": "workbench", "status": "failed" if mode == "badinit" else "connected"}}]}})
    if mode == "plain":
        finish("msg_2", "plain"); continue
    if mode == "badinit":
        time.sleep(60); continue
    if mode == "late":       # the second call of a batch arrives well after the first one's approval card was shown
        ia = make(0, "toolu_a"); out = {{}}
        th = threading.Thread(target=lambda: out.__setitem__("a", mcp("call_operation", ia, "toolu_a"))); th.start()
        time.sleep(2.0)
        ib = make(1, "toolu_b"); out["b"] = mcp("call_operation", ib, "toolu_b"); th.join()
        tool_result("toolu_a", out["a"]); tool_result("toolu_b", out["b"]); finish("msg_2", "late done"); continue
    if mode == "dup":        # the same tool_use id sent twice (a client retry)
        ia = make(0, "toolu_d"); res = {{}}
        th = threading.Thread(target=lambda: res.__setitem__(1, mcp("call_operation", ia, "toolu_d"))); th.start()
        time.sleep(0.3); res[2] = mcp("call_operation", ia, "toolu_d"); th.join()
        tool_result("toolu_d", res[1]); finish("msg_2", "second said: " + res[2]["content"][0]["text"]); continue
    if mode == "strarg":
        r = mcp("call_operation", {{"operation_id": "study_create_api_study_create_post", "body": "this is not json"}}, "toolu_s")
        finish("msg_2", "got: " + r["content"][0]["text"]); continue
    if mode == "noid":
        inp = make(1, "toolu_noid"); res = mcp("call_operation", inp, None); tool_result("toolu_noid", res); finish("msg_2", "tried")
    elif mode == "read":
        inp = {{"query": "study"}}; tool_use("msg_1", "toolu_r", "list_operations", inp)
        tool_result("toolu_r", mcp("list_operations", inp, "toolu_r")); finish("msg_2", "listed")
    elif mode in ("parallel", "parallel_held"):
        # "parallel": each result is printed as soon as its call finishes. "parallel_held": Claude holds every
        # finished result back until the whole batch is done (so a read's result cannot be seen while a change waits).
        ids = ["toolu_a", "toolu_b"]; inps = [make(i, u) for i, u in enumerate(ids)]
        rd = {{"query": "study"}}; tool_use("msg_1", "toolu_r", "list_operations", rd)
        out = {{}}
        def run(name, inp, u):
            res = mcp(name, inp, u)
            out[u] = res
            if mode == "parallel":
                tool_result(u, res)
        ts = [threading.Thread(target=run, args=("call_operation", inps[i], ids[i])) for i in range(2)]
        ts.append(threading.Thread(target=run, args=("list_operations", rd, "toolu_r")))
        [t.start() for t in ts]; [t.join() for t in ts]
        if mode == "parallel_held":
            for u in ["toolu_r", *ids]:
                tool_result(u, out[u])
        finish("msg_2", "parallel done")
    else:  # create
        inp = make(0, "toolu_c"); res = mcp("call_operation", inp, "toolu_c")
        tool_result("toolu_c", res); finish("msg_2", "created" if not res.get("isError") else "refused")
'''


@pytest.fixture
def stub(tmp_path, monkeypatch):
    path = tmp_path / "claude"
    path.write_text(_STUB.format(py=sys.executable))
    path.chmod(0o755)
    monkeypatch.setattr(claude_cli, "installed", lambda: str(path))
    monkeypatch.setattr(claude_cli, "logged_in", lambda: True)
    return path


def turn(client, **body):
    body.setdefault("messages", [])
    with client.stream("POST", "/api/chat/turn", json=body) as r:
        if r.status_code != 200:
            return r.status_code, json.loads(r.read())
        return 200, [f for f in (json.loads(line) for line in r.iter_lines() if line.strip()) if f["type"] != "ping"]


def types(frames):
    return [f["type"] for f in frames]


def settle(client, frames, decide):
    """Answer every approval card as it appears until the turn is done, and return ALL the frames. A batch of parallel
    changes may show its cards together or one after the other (a slow runner delivers the second call late), and
    neither is wrong: what must hold is that every change gets a card and nothing runs unanswered."""
    seen = list(frames)
    cur = frames
    for _ in range(6):
        if not cur or cur[-1]["type"] != "done" or not cur[-1]["pending_approval"]:
            break
        cards = [f["tool_call_id"] for f in cur if f["type"] == "approval-required"]
        code, cur = turn(client, messages=cur[-1]["messages"], mode="agent",
                         deferred_results={"approvals": {c: decide(c) for c in cards}})
        assert code == 200, cur
        seen += cur
    return seen


def audit(ws):
    p = ws / ".pbg" / "ai-actions.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


# --- stub: coordination --------------------------------------------------------------------------------


def test_a_change_waits_for_the_user_then_runs_once_approved(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "create")
    code, f1 = turn(client, prompt="make a study", mode="agent")
    assert code == 200
    assert types(f1) == ["tool-call", "approval-required", "done"]
    card = f1[1]
    assert card["tool_name"] == "call_operation" and card["tool_call_id"] == "toolu_c"
    assert card["metadata"]["method"] == "POST" and card["metadata"]["path"] == "/api/study-create"
    assert card["metadata"]["body"] == {"name": "stub-0"} and f1[2]["pending_approval"] is True
    assert not (ws / "studies" / "stub-0").exists()                       # NOT done while it waits
    assert not [a for a in audit(ws) if a.get("phase") == "intent"]       # and not even claimed
    (pid,) = claude_cli._LIVE_PIDS
    transcript = ModelMessagesTypeAdapter.validate_python(f1[2]["messages"])
    assert isinstance(transcript[-1], ModelResponse) and any(isinstance(p, ToolCallPart) for p in transcript[-1].parts)

    code, f2 = turn(client, messages=f1[2]["messages"], mode="agent",
                    deferred_results={"approvals": {"toolu_c": True}})
    assert code == 200
    assert types(f2)[0] == "tool-result" and f2[0]["content"]["status"] == 200 and f2[0]["ok"] is True
    assert f2[-1]["type"] == "done" and f2[-1]["pending_approval"] is False
    assert "created" in "".join(f.get("text", "") for f in f2 if f["type"] == "text-delta")
    assert (ws / "studies" / "stub-0").is_dir()                            # now it ran
    phases = [(a["phase"], a.get("status")) for a in audit(ws) if a.get("phase") in ("intent", "result")]
    assert phases == [("intent", None), ("result", 200)]                   # intent-first audit, as for every provider
    final = ModelMessagesTypeAdapter.validate_python(f2[-1]["messages"])
    assert any(isinstance(p, ToolReturnPart) and p.tool_call_id == "toolu_c" for m in final for p in m.parts)
    assert claude_cli._LIVE_PIDS == {pid}                                  # the SAME process served the whole turn


def test_a_declined_change_never_runs_and_the_model_is_told(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "create")
    _, f1 = turn(client, prompt="make a study", mode="agent")
    _, f2 = turn(client, messages=f1[-1]["messages"], mode="agent",
                 deferred_results={"approvals": {"toolu_c": {"denied": "not now"}}})
    res = f2[0]
    assert res["type"] == "tool-result" and res["ok"] is True
    assert "declined" in res["content"]["error"] and "not now" in res["content"]["error"] and "NOT done" in res["content"]["error"]
    assert not (ws / "studies" / "stub-0").exists()
    assert not [a for a in audit(ws) if a.get("phase") in ("intent", "result")]      # nothing was claimed or run


def test_parallel_changes_pause_together_after_the_reads_finish_and_need_one_answer(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "parallel")
    _, f1 = turn(client, prompt="two studies", mode="agent")
    t = types(f1)
    assert t.index("tool-result") < t.index("approval-required")             # the read finished before the first pause
    code, err = turn(client, messages=f1[-1]["messages"], mode="agent", deferred_results={"approvals": {"toolu_zzz": True}})
    assert code == 422 and "not pending" in err["error"]                      # an id that is not pending is refused up front
    every = settle(client, f1, lambda cid: True if cid == "toolu_a" else {"denied": "no"})
    assert {f["tool_call_id"] for f in every if f["type"] == "approval-required"} == {"toolu_a", "toolu_b"}   # each got a card
    assert every[-1]["type"] == "done" and every[-1]["pending_approval"] is False
    assert (ws / "studies" / "stub-0").is_dir() and not (ws / "studies" / "stub-1").exists()


def test_a_call_that_arrives_after_the_pause_gets_its_own_card_instead_of_wedging_the_chat(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "late")
    _, f1 = turn(client, prompt="two studies", mode="agent")
    assert [f["tool_call_id"] for f in f1 if f["type"] == "approval-required"] == ["toolu_a"]     # B had not arrived yet
    time.sleep(3.0)                                                          # now B arrives: pending is {A, B}, only A has a card
    code, f2 = turn(client, messages=f1[-1]["messages"], mode="agent", deferred_results={"approvals": {"toolu_a": True}})
    assert code == 200                                                       # answering the card the user can see is accepted
    assert [f["tool_call_id"] for f in f2 if f["type"] == "approval-required"] == ["toolu_b"]    # and B's card follows
    assert f2[-1]["pending_approval"] is True
    assert (ws / "studies" / "stub-0").is_dir() and not (ws / "studies" / "stub-1").exists()
    code, f3 = turn(client, messages=f2[-1]["messages"], mode="agent", deferred_results={"approvals": {"toolu_b": True}})
    assert code == 200 and f3[-1]["type"] == "done" and f3[-1]["pending_approval"] is False
    assert (ws / "studies" / "stub-1").is_dir()


def test_a_repeated_tool_use_id_cannot_displace_or_duplicate_a_pending_change(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "dup")
    _, f1 = turn(client, prompt="make it", mode="agent")
    assert [f["tool_call_id"] for f in f1 if f["type"] == "approval-required"] == ["toolu_d"]     # one card, not two
    _, f2 = turn(client, messages=f1[-1]["messages"], mode="agent", deferred_results={"approvals": {"toolu_d": True}})
    text = "".join(f.get("text", "") for f in f2 if f["type"] == "text-delta")
    assert "already waiting" in text                                         # the duplicate was refused, not queued
    assert (ws / "studies" / "stub-0").is_dir()
    assert [a["phase"] for a in audit(ws) if a.get("phase") in ("intent", "result")] == ["intent", "result"]   # ran once


def test_string_arguments_are_refused_like_the_other_providers_refuse_them(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "strarg")
    _, frames = turn(client, prompt="go", mode="agent")
    assert "approval-required" not in types(frames)
    assert "must each be a JSON object" in "".join(f.get("text", "") for f in frames if f["type"] == "text-delta")
    assert not (ws / "studies").exists() or not any((ws / "studies").iterdir())


def test_claude_that_cannot_reach_the_tool_server_is_an_error_not_a_silent_chat_without_tools(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "badinit")
    _, frames = turn(client, prompt="list studies", mode="ask")
    assert types(frames) == ["error"] and "could not connect to the workbench's tool server" in frames[0]["error"]
    assert not claude_cli._LIVE_PIDS and not claude_mcp.BINDINGS             # killed, and its token forgotten


def test_ask_and_agent_say_so_up_front_when_the_mcp_package_is_missing(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setattr(claude_mcp, "available", lambda: False)
    for mode in ("ask", "agent"):
        code, err = turn(client, prompt="hi", mode=mode)
        assert code == 503 and "mcp" in err["error"]
    monkeypatch.setenv("VW_STUB_MODE", "plain")
    code, frames = turn(client, prompt="hi", mode="manual")                  # Manual needs no tools: unaffected
    assert code == 200 and frames[-1]["type"] == "done"


def test_serve_gives_uvicorn_a_graceful_shutdown_timeout():
    """Without one uvicorn waits for ever for open requests BEFORE it runs lifespan shutdown, so an open approval would
    keep a Ctrl-C from finishing (the test above proves a stop completes with this very constant). Checked from the
    source, not by running `serve_fastapi`: that starts process-lifetime background threads (the remote-link probe,
    the cache warmer) which would leak into every later test in the same process."""
    import inspect
    assert "timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S" in inspect.getsource(startup.serve_fastapi)
    assert startup.GRACEFUL_SHUTDOWN_S > 0


def test_the_process_gets_the_tool_flags_a_private_0600_config_and_long_timeouts_and_no_token_in_argv(server, stub, monkeypatch, tmp_path):
    client, ws, _ = server
    out = tmp_path / "handed-over.json"
    monkeypatch.setenv("VW_STUB_OUT", str(out))
    monkeypatch.setenv("VW_STUB_MODE", "read")
    turn(client, prompt="list", mode="ask")
    seen = json.loads(out.read_text())
    a = seen["argv"]
    assert a[a.index("--tools") + 1] == "Skill" and "--strict-mcp-config" in a
    assert a[a.index("--permission-prompts") + 1] == "none" and a[a.index("--setting-sources") + 1] == "user"
    assert sorted(a[a.index("--allowedTools") + 1].split(",")) == sorted([*(claude_mcp.TOOL_PREFIX + n for n in claude_mcp.TOOL_NAMES), "Skill"])
    token = json.loads(seen["cfg"])["mcpServers"]["workbench"]["headers"]["Authorization"].removeprefix("Bearer ")
    assert token and all(token not in arg for arg in a)                      # the secret is in the 0600 file, never in argv
    assert seen["cfg_mode"] == "0o600"
    assert all(int(t) >= (claude_cli.TURN_MAX_S + 300) * 1000 for t in seen["timeouts"])   # the CLI's 5-minute default would abort an approval
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CLAUDE_USER_SETTINGS", "0")     # the opt-out: no plugins / hooks / CLAUDE.md
    assert claude_cli.setting_sources() == ""


def test_stopping_the_server_with_an_approval_open_does_not_hang_and_leaves_no_process(server, stub, monkeypatch):
    client, ws, app = server
    monkeypatch.setenv("VW_STUB_MODE", "create")
    _, f1 = turn(client, prompt="make a study", mode="agent")
    assert f1[-1]["pending_approval"] is True and claude_cli._LIVE_PIDS
    pids = set(claude_cli._LIVE_PIDS)
    srv, thread = app.state._test_server
    srv.should_exit = True
    thread.join(startup.GRACEFUL_SHUTDOWN_S + 15)
    assert not thread.is_alive()                                             # the held-open MCP request did not block the stop
    time.sleep(1.0)
    assert not any(_alive(p) for p in pids) and not claude_mcp.BINDINGS
    assert not (ws / "studies" / "stub-0").exists()                          # and the change was never run


def test_a_new_prompt_while_an_approval_is_pending_starts_fresh_and_the_old_change_never_runs(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "create")
    _, f1 = turn(client, prompt="make a study", mode="agent")
    monkeypatch.setenv("VW_STUB_MODE", "read")
    code, f2 = turn(client, prompt="never mind, just list", messages=f1[-1]["messages"], mode="agent")
    assert code == 200 and f2[-1]["type"] == "done" and "approval-required" not in types(f2)
    assert not (ws / "studies" / "stub-0").exists()
    code, err = turn(client, messages=f2[-1]["messages"], mode="agent", deferred_results={"approvals": {"toolu_c": True}})
    assert code in (409, 422)                                                # the abandoned card cannot be approved from here
    assert not (ws / "studies" / "stub-0").exists()


def test_parallel_changes_pause_even_when_claude_holds_finished_results_back(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "parallel_held")
    _, f1 = turn(client, prompt="two studies", mode="agent")
    t = types(f1)
    assert t.count("approval-required") >= 1 and t[-1] == "done" and f1[-1]["pending_approval"] is True
    assert "tool-result" not in t                                           # nothing was printed: the batch is incomplete
    every = settle(client, f1, lambda cid: True)
    assert {f["tool_call_id"] for f in every if f["type"] == "approval-required"} == {"toolu_a", "toolu_b"}
    assert types(every).count("tool-result") == 3 and every[-1]["type"] == "done" and every[-1]["pending_approval"] is False
    assert (ws / "studies" / "stub-0").is_dir() and (ws / "studies" / "stub-1").is_dir()
    merged = ModelMessagesTypeAdapter.validate_python(every[-1]["messages"])
    returns = [m for m in merged if any(isinstance(p, ToolReturnPart) for p in m.parts)]
    assert len(returns) == 1 and len(returns[0].parts) == 3                 # one request carrying all three returns


def test_a_resume_with_nothing_parked_or_stale_ids_is_refused_before_streaming(server, stub, monkeypatch):
    client, ws, _ = server
    code, err = turn(client, messages=[], mode="agent", deferred_results={"approvals": {"toolu_x": True}})
    assert code == 409 and "expired" in err["error"]
    monkeypatch.setenv("VW_STUB_MODE", "create")
    _, f1 = turn(client, prompt="make a study", mode="agent")
    code, err = turn(client, messages=f1[-1]["messages"], mode="agent", deferred_results={"approvals": {"toolu_zzz": True}})
    assert code == 422 and "toolu_c" in err["error"]
    code, err = turn(client, messages=f1[-1]["messages"], mode="manual", deferred_results={"approvals": {"toolu_c": True}})
    assert code == 422 and "nothing to approve" in err["error"]


def test_an_unanswered_approval_expires_with_its_process_and_the_change_never_runs(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setattr(claude_cli, "IDLE_S", 1.5)
    monkeypatch.setenv("VW_STUB_MODE", "create")
    _, f1 = turn(client, prompt="make a study", mode="agent")
    assert claude_cli._LIVE_PIDS and claude_mcp.BINDINGS
    (pid,) = claude_cli._LIVE_PIDS
    time.sleep(4)
    assert not _alive(pid) and not claude_mcp.BINDINGS                    # the process and its token are gone
    code, err = turn(client, messages=f1[-1]["messages"], mode="agent", deferred_results={"approvals": {"toolu_c": True}})
    assert code == 409
    assert not (ws / "studies" / "stub-0").exists() and not audit(ws)


def test_ask_mode_cannot_change_anything_and_there_is_nothing_to_approve(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "create")
    _, frames = turn(client, prompt="make a study", mode="ask")
    assert "approval-required" not in types(frames) and frames[-1]["type"] == "done"
    res = next(f for f in frames if f["type"] == "tool-result")
    assert "read-only" in res["content"]["error"]
    assert not (ws / "studies").exists() or not any((ws / "studies").iterdir())


def test_reads_run_without_approval_in_ask_mode(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "read")
    _, frames = turn(client, prompt="list", mode="ask")
    res = next(f for f in frames if f["type"] == "tool-result")
    assert res["ok"] is True and res["content"]["total"] > 0 and "approval-required" not in types(frames)
    ops = [o["method"] for o in res["content"]["operations"]]
    assert set(ops) == {"GET"}                                             # Ask mode never even lists a write


def test_a_change_that_cannot_be_tied_to_an_approval_card_is_refused(server, stub, monkeypatch):
    client, ws, _ = server
    monkeypatch.setenv("VW_STUB_MODE", "noid")
    _, frames = turn(client, prompt="make a study", mode="agent")
    assert "approval-required" not in types(frames)
    res = next(f for f in frames if f["type"] == "tool-result")
    assert "cannot be tied to an approval card" in res["content"]["error"]
    assert not (ws / "studies" / "stub-1").exists()


def test_the_mcp_endpoint_only_answers_a_registered_token_and_forgets_it_with_the_process(server):
    client, ws, app = server
    url = claude_mcp.MOUNT + "/"
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post(url, json=rpc, headers=hdr).status_code == 401
    assert client.post(url, json=rpc, headers={**hdr, "Authorization": "Bearer not-a-token"}).status_code == 401
    deps = ai_tools.ChatDeps(app=app, client=ai_tools.make_client(app), ws_root=ws, session_key=None,
                             provider="claude-code", model="haiku", mode="agent")
    token, _ = claude_mcp.register(deps, 60.0)
    ok = client.post(url, json=rpc, headers={**hdr, "Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    # the server registers the command tools too; they reach the model only when the server was started with the
    # explicit switch (--disallowedTools hides them otherwise), and every handler re-checks its gates
    assert sorted(t["name"] for t in ok.json()["result"]["tools"]) == sorted(claude_mcp.TOOL_NAMES + chat_commands.TOOL_NAMES)
    claude_mcp.unregister(token)
    assert client.post(url, json=rpc, headers={**hdr, "Authorization": f"Bearer {token}"}).status_code == 401


# --- real claude ---------------------------------------------------------------------------------------


@live
def test_real_claude_reads_the_workspace_through_the_tools(server):
    client, ws, _ = server
    assert client.post("/api/study-create", json={"name": "alpha-study"}).status_code == 200
    # No workspace summary: otherwise the model can (rightly) answer from it without calling a tool at all.
    code, frames = turn(client, prompt="Call the study-listing operation with your tools (find it with list_operations if you "
                                       "need to) and tell me the name of the study in this workspace. Reply with just the name.",
                        mode="ask", include_manifest=False)
    assert code == 200 and frames[-1]["type"] == "done" and "approval-required" not in types(frames)
    assert any(f["type"] == "tool-call" for f in frames) and any(f["type"] == "tool-result" and f["ok"] for f in frames)
    assert "alpha-study" in "".join(f.get("text", "") for f in frames if f["type"] == "text-delta")


@live
def test_real_claude_in_ask_mode_cannot_change_the_workspace(server):
    client, ws, _ = server
    _, frames = turn(client, prompt="Create a new study named should-not-exist using the study-create operation. Do it now.", mode="ask")
    assert "approval-required" not in types(frames) and frames[-1]["type"] == "done"
    assert not (ws / "studies" / "should-not-exist").exists()


@live
def test_real_claude_in_agent_mode_waits_for_approval_then_makes_the_change(server):
    client, ws, _ = server
    _, f1 = turn(client, prompt=f"Call call_operation with operation_id {CREATE} and body {{\"name\": \"live-study\"}}, then confirm.",
                 mode="agent", include_manifest=False)
    cards = [f for f in f1 if f["type"] == "approval-required"]
    assert len(cards) == 1 and cards[0]["metadata"]["path"] == "/api/study-create" and f1[-1]["pending_approval"] is True
    assert not (ws / "studies" / "live-study").exists()
    _, f2 = turn(client, messages=f1[-1]["messages"], mode="agent",
                 deferred_results={"approvals": {cards[0]["tool_call_id"]: True}})
    assert f2[-1]["type"] == "done" and f2[-1]["pending_approval"] is False
    assert (ws / "studies" / "live-study").is_dir()
    assert [a["phase"] for a in audit(ws) if a.get("phase") in ("intent", "result")] == ["intent", "result"]


@live
def test_real_claude_in_agent_mode_respects_a_refusal(server):
    client, ws, _ = server
    _, f1 = turn(client, prompt=f"Call call_operation with operation_id {CREATE} and body {{\"name\": \"refused-study\"}}.",
                 mode="agent", include_manifest=False)
    cards = [f for f in f1 if f["type"] == "approval-required"]
    assert len(cards) == 1
    _, f2 = turn(client, messages=f1[-1]["messages"], mode="agent",
                 deferred_results={"approvals": {cards[0]["tool_call_id"]: {"denied": "not now"}}})
    assert f2[-1]["type"] == "done"
    assert not (ws / "studies" / "refused-study").exists()
    assert not [a for a in audit(ws) if a.get("phase") in ("intent", "result")]


# --- opt-in plugin allowlist (VIVARIUM_WORKBENCH_CLAUDE_PLUGINS) -----------------------------------------


_ATTACH = claude_cli.Attach(mcp_config="{}", allowed=("mcp__workbench__a", "mcp__workbench__b"))
_DEFAULT_ASK_ARGV = [
    "claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
    "--tools", "Skill", "--strict-mcp-config", "--mcp-config", "/cfg.json",
    "--allowedTools", "mcp__workbench__a,mcp__workbench__b,Skill", "--permission-prompts", "none", "--setting-sources", "user",
    "--no-session-persistence", "--model", "sonnet", "--system-prompt", "SYS"]


def test_default_argv_is_unchanged_by_the_allowlist_feature(monkeypatch):
    """Falsifies: the opt-in changed what an unconfigured install spawns. The expected list is the argv as it was before
    this feature (literal, not derived from the builder), and the unset / empty / ``all`` env values all mean 'default'."""
    for value in (None, "", "all", " ALL "):
        monkeypatch.delenv("VIVARIUM_WORKBENCH_CLAUDE_PLUGINS", raising=False)
        if value is not None:
            monkeypatch.setenv("VIVARIUM_WORKBENCH_CLAUDE_PLUGINS", value)
        assert claude_cli.plugin_allowlist() is None
    assert claude_cli.build_argv("claude", "sonnet", "SYS", "/cfg.json", _ATTACH) == _DEFAULT_ASK_ARGV
    assert claude_cli.build_argv("claude", "sonnet", "SYS", "/cfg.json", _ATTACH, None) == _DEFAULT_ASK_ARGV


def test_plain_chat_argv_ignores_the_allowlist():
    """Falsifies: a plain chat (no tools) could pick up plugins. It never reads settings, whatever ``plugin_dirs`` says."""
    a = claude_cli.build_argv("claude", "sonnet", "SYS", plugin_dirs=("/p/one",))
    assert a[a.index("--setting-sources") + 1] == "" and "--plugin-dir" not in a


def test_allowlist_reads_no_user_settings_and_loads_exactly_the_given_directories():
    a = claude_cli.build_argv("claude", "sonnet", "SYS", "/cfg.json", _ATTACH, ("/p/one", "/p/two"))
    assert a[a.index("--setting-sources") + 1] == ""
    assert [a[i + 1] for i, x in enumerate(a) if x == "--plugin-dir"] == ["/p/one", "/p/two"]
    assert a[a.index("--tools") + 1] == "Skill" and "--strict-mcp-config" in a     # the safety net is untouched
    assert claude_cli.build_argv("claude", "sonnet", "SYS", "/cfg.json", _ATTACH, ()).count("--plugin-dir") == 0


@pytest.mark.parametrize("raw", ["viva; rm -rf ~", "../../etc", "a b", "$(id)", "viva@", "@x", ",", "x" * 80, "a\nb", "-rf"])
def test_allowlist_refuses_names_that_are_not_plain_plugin_ids(monkeypatch, raw):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CLAUDE_PLUGINS", raw)
    with pytest.raises(claude_cli.ClaudeCliError):
        claude_cli.plugin_allowlist()


def test_allowlist_parses_none_and_names(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CLAUDE_PLUGINS", "none")
    assert claude_cli.plugin_allowlist() == ()
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CLAUDE_PLUGINS", "viva-superpowers, slack@claude-plugins-official")
    assert claude_cli.plugin_allowlist() == ("viva-superpowers", "slack@claude-plugins-official")


def _enabled_plugins():
    import subprocess
    out = subprocess.run(["claude", "plugin", "list", "--json"], capture_output=True, text=True, timeout=30, cwd="/").stdout
    return [r for r in json.loads(out) if r.get("enabled") and os.path.isdir(r.get("installPath", ""))]


@live
def test_real_cli_resolves_installed_plugins_and_refuses_unknown_ones():
    """Falsifies: the resolver trusts a fixture. It asks the real ``claude plugin list --json``."""
    rows = _enabled_plugins()
    if not rows:
        pytest.skip("no enabled Claude Code plugin installed")
    exe = claude_cli.installed()
    r = rows[0]
    assert r["installPath"] in claude_cli.resolve_plugin_dirs(exe, (r["id"],))
    assert r["installPath"] in claude_cli.resolve_plugin_dirs(exe, (r["id"].split("@")[0],))
    with pytest.raises(claude_cli.ClaudeCliError):
        claude_cli.resolve_plugin_dirs(exe, ("no-such-plugin-xyz",))


@live
def test_real_claude_with_an_allowlist_loads_only_that_plugin_and_fewer_tokens(tmp_path):
    """Falsifies: the restriction is cosmetic. Two real first turns with the workbench's own argv (tiny prompt, haiku): the
    default one loads the user's whole plugin set; the allowlisted one loads the named plugin and not the others, and its
    first turn is smaller. (Skipped when fewer than two plugins are enabled: nothing to restrict.)"""
    import subprocess
    rows = _enabled_plugins()
    if len(rows) < 2:
        pytest.skip("needs two or more enabled Claude Code plugins")
    exe = claude_cli.installed()
    keep = rows[0]
    dirs = claude_cli.resolve_plugin_dirs(exe, (keep["id"],))
    cfg = tmp_path / ".mcp.json"
    cfg.write_text('{"mcpServers":{}}')
    attach = claude_cli.Attach(mcp_config="{}", allowed=())

    def first_turn(plugin_dirs):
        argv = claude_cli.build_argv(exe, "haiku", "Reply briefly.", str(cfg), attach, plugin_dirs)
        msg = json.dumps({"type": "user", "message": {"role": "user", "content": "Reply with one word: ok"}}) + "\n"
        p = subprocess.run(argv, input=msg, capture_output=True, text=True, cwd=tmp_path, timeout=180, env=claude_cli.child_env())
        evs = [json.loads(line) for line in p.stdout.splitlines() if line.startswith("{")]
        init = next(e for e in evs if e.get("type") == "system" and e.get("subtype") == "init")
        u = next(e for e in evs if e.get("type") == "result")["usage"]
        tokens = u["input_tokens"] + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
        return {x["name"] if isinstance(x, dict) else x for x in init["plugins"]}, tokens

    full_plugins, full_tokens = first_turn(None)
    only_plugins, only_tokens = first_turn(dirs)
    name = keep["id"].split("@")[0]
    others = {r["id"].split("@")[0] for r in rows[1:]} - {name}
    assert name in full_plugins and name in only_plugins
    assert others & full_plugins and not others & only_plugins
    assert only_tokens < full_tokens

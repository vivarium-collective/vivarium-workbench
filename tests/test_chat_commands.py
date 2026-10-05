"""The chat's command tools (``lib/chat_commands.py``): the gates, the cards, the trust flow, the audit, and what
happens between the user's click and the command.

The approval is the REAL coordinator the Claude Code provider uses (``claude_mcp.Coordinator``): a handler blocks on
its card and the test plays the user by deciding it. Commands are run for real; the "user" is the only fake.
"""
import asyncio
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("pydantic_ai")
from vivarium_workbench.lib import ai_tools, chat_commands, claude_cli, claude_mcp, run_command, user_state  # noqa: E402
from vivarium_workbench.lib.claude_mcp import Binding, Decision  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "a.txt").write_text("alpha\nbeta\n")
    (ws / "sub" / "b.txt").write_text("bee\n")
    (ws / ".pbg").mkdir()
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENABLE_RUN_COMMAND", "1")
    return ws, cfg


def binding(ws: Path, **kw) -> Binding:
    deps = ai_tools.ChatDeps(app=None, client=None, ws_root=ws, session_key="sess", provider="claude-code",   # type: ignore[arg-type]
                             model="m", mode=kw.pop("mode", "agent"), local_only=kw.pop("local_only", True))
    return Binding(deps=deps, approval_ttl=10)


async def _answer(b: Binding, call_id: str, decision: Decision | None, between=None):
    """Wait for the card of ``call_id``, return its payload, run ``between``, then play the user's answer."""
    for _ in range(400):
        if call_id in b.coord.pending:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("no approval card appeared")
    meta = b.coord.pending[call_id].metadata
    if between:
        between()
    if decision is not None:
        b.coord.decide({call_id: decision})
    return meta


def go(coro):
    return asyncio.run(coro)


def run_cmd(b: Binding, call_id: str, argv, decision: Decision | None = Decision(True), between=None, **kw):
    async def main():
        task = asyncio.ensure_future(chat_commands.command_call(b, call_id, argv, kw.get("cwd"), kw.get("extra_dirs")))
        meta = await _answer(b, call_id, decision, between)
        return meta, await task
    return go(main())


def trusted(ws: Path) -> None:
    user_state.grant_trust(ws)


def log_lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()] if path.is_file() else []


# --- the gates ------------------------------------------------------------------------------------------------


def test_off_unless_the_server_was_started_with_the_switch(env, monkeypatch):
    ws, _ = env
    monkeypatch.delenv("VIVARIUM_WORKBENCH_ENABLE_RUN_COMMAND")
    trusted(ws)
    out = go(chat_commands.command_call(binding(ws), "t1", ["ls"], None, None))
    assert "switched off" in out["error"] and not chat_commands.enabled()
    assert "switched off" in go(chat_commands.trust_call(binding(ws), "t2"))["error"]


def test_only_agent_mode_and_only_a_local_binding(env):
    ws, _ = env
    trusted(ws)
    assert "Agent mode" in go(chat_commands.command_call(binding(ws, mode="ask"), "t", ["ls"], None, None))["error"]
    assert "local" in go(chat_commands.command_call(binding(ws, local_only=False), "t", ["ls"], None, None))["error"]


def test_a_refused_command_never_asks_the_user(env):
    ws, _ = env
    trusted(ws)
    b = binding(ws)
    for argv in (["python", "-c", "1"], ["cat", ".env"], ["ls", "/etc"], ["git", "-c", "x=y", "status"]):
        out = go(chat_commands.command_call(b, "t", argv, None, None))
        assert out["error"].startswith("refused:"), (argv, out)
    assert not b.coord.pending


def test_an_untrusted_workspace_is_told_to_ask_and_no_card_for_the_command_appears(env):
    ws, _ = env
    b = binding(ws)
    out = go(chat_commands.command_call(b, "t", ["ls"], None, None))
    assert "not trusted" in out["error"] and "request_workspace_trust" in out["error"] and not b.coord.pending


def test_a_call_without_an_id_cannot_be_tied_to_a_card(env):
    ws, _ = env
    assert "no id" in go(chat_commands.trust_call(binding(ws), ""))["error"]       # untrusted: it would need a card
    trusted(ws)
    assert "no id" in go(chat_commands.command_call(binding(ws), "", ["ls"], None, None))["error"]
    assert go(chat_commands.trust_call(binding(ws), "")) == {"trusted": True, "note": "this workspace is already trusted"}


# --- trust ----------------------------------------------------------------------------------------------------


def test_trust_needs_the_users_explicit_answer_and_is_stored_outside_the_workspace(env):
    ws, cfg = env
    b = binding(ws)

    async def main():
        task = asyncio.ensure_future(chat_commands.trust_call(b, "tr"))
        meta = await _answer(b, "tr", Decision(False, reason="not this one"))
        return meta, await task
    meta, out = go(main())
    ef = meta["effect"]
    assert ef["kind"] == "trust" and ef["workspace"] == os.path.realpath(ws) and str(cfg) in ef["stored"]
    assert out["trusted"] is False and not user_state.is_trusted(ws)          # a refusal grants nothing

    async def approve():
        task = asyncio.ensure_future(chat_commands.trust_call(b, "tr2"))
        await _answer(b, "tr2", Decision(True))
        return await task
    assert go(approve()) == {"trusted": True}
    assert user_state.is_trusted(ws)
    assert (cfg / "trusted-workspaces.json").is_file()
    assert not list(ws.rglob("*trust*")), "trust must never be written inside the workspace"
    assert {r["phase"] for r in log_lines(user_state.command_log_path(ws))} == {"intent", "result"}


def test_nothing_inside_a_workspace_can_grant_it_trust(env):
    ws, _ = env
    for rel in ("trusted-workspaces.json", ".pbg/trusted-workspaces.json", ".pbg/trust.json", "trusted"):
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"trusted": {user_state.workspace_id(ws): {"path": os.path.realpath(ws)}}}))
    assert not user_state.is_trusted(ws)


def test_trust_is_for_that_exact_real_path(env, tmp_path):
    ws, _ = env
    user_state.grant_trust(ws)
    link = tmp_path / "alias"
    link.symlink_to(ws)
    other = tmp_path / "other"
    other.mkdir()
    assert user_state.is_trusted(ws) and user_state.is_trusted(link)       # the alias IS the same real folder
    assert not user_state.is_trusted(other) and not user_state.is_trusted(tmp_path)
    assert user_state.revoke_trust(ws) and not user_state.is_trusted(ws)


# --- the command flow -----------------------------------------------------------------------------------------


def test_the_card_shows_what_runs_and_an_approved_command_runs_and_is_recorded(env):
    ws, cfg = env
    trusted(ws)
    b = binding(ws)
    meta, out = run_cmd(b, "c1", ["grep", "-n", "beta", "a.txt"])
    ef = meta["effect"]
    # the card carries the program and EVERY argument that runs (the runner resolves paths and adds -e)
    assert ef["kind"] == "command" and ef["command_line"][0].endswith("/grep")
    assert ef["command_line"][1:] == ["-n", "-e", "beta", "--", str(ws.resolve() / "a.txt")]
    assert ef["requested"] == ["grep", "-n", "beta", "a.txt"] and ef["cwd"] == str(ws.resolve())
    assert ef["extra_dirs"] == [] and ef["limits"]["timeout_s"] == run_command.TIMEOUT_S and meta["body"] is None
    assert out["exit_code"] == 0 and out["stdout"].strip() == "2:beta"
    for log in (ws / ".pbg" / "ai-actions.jsonl", user_state.command_log_path(ws)):
        recs = log_lines(log)
        assert [r["phase"] for r in recs] == ["intent", "result"], log
        assert recs[0]["tool_call_id"] == "c1" and recs[0]["command_line"] == ef["command_line"]
        assert recs[1]["exit_code"] == 0 and recs[1]["outcome"] == "ran"


def test_a_declined_command_does_not_run_and_leaves_no_intent_record(env):
    ws, _ = env
    trusted(ws)
    b = binding(ws)
    marker = ws / "marker"
    meta, out = run_cmd(b, "c2", ["find", ".", "-name", "a.txt"], Decision(False, reason="no"))
    assert "declined" in out["error"] and "NOT done" in out["error"] and "stdout" not in out
    assert not log_lines(ws / ".pbg" / "ai-actions.jsonl") and not log_lines(user_state.command_log_path(ws))
    assert not marker.exists()


def test_a_second_card_for_the_same_call_id_is_refused(env):
    ws, _ = env
    trusted(ws)
    b = binding(ws)

    async def main():
        t1 = asyncio.ensure_future(chat_commands.command_call(b, "dup", ["ls"], None, None))
        await _answer(b, "dup", None)
        second = await chat_commands.command_call(b, "dup", ["ls", "-l"], None, None)
        b.coord.decide({"dup": Decision(False)})
        await t1
        return second
    assert "already waiting" in go(main())["error"]


def test_what_was_approved_is_revalidated_so_a_swapped_folder_does_not_run(env, tmp_path):
    ws, _ = env
    trusted(ws)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "b.txt").write_text("SECRET-OUTSIDE\n")
    b = binding(ws)

    def swap():                                    # between the card and the click, `sub` becomes a link out of the workspace
        (ws / "sub" / "b.txt").unlink()
        (ws / "sub").rmdir()
        (ws / "sub").symlink_to(outside)
    _, out = run_cmd(b, "c3", ["cat", "sub/b.txt"], between=swap)
    assert "error" in out and "SECRET-OUTSIDE" not in json.dumps(out)
    assert not log_lines(user_state.command_log_path(ws)), "an unrun command must leave no intent record"


def test_trust_revoked_between_card_and_click_stops_the_command(env):
    ws, _ = env
    trusted(ws)
    _, out = run_cmd(binding(ws), "c4", ["ls"], between=lambda: user_state.revoke_trust(ws))
    assert "error" in out and "stdout" not in out


def test_a_command_is_not_run_when_it_cannot_be_recorded(env, tmp_path):
    ws, cfg = env
    trusted(ws)
    # the protected copy cannot be written: the folder it needs is a file
    (cfg / "command-log").write_text("not a folder")
    _, out = run_cmd(binding(ws), "c5", ["cat", "a.txt"])
    assert "audit log unavailable" in out["error"] and "alpha" not in json.dumps(out)


def test_the_workspace_audit_log_is_required_too(env):
    ws, _ = env
    trusted(ws)
    (ws / ".pbg").rmdir()
    (ws / ".pbg").write_text("a file where the audit folder should be")
    _, out = run_cmd(binding(ws), "c6", ["cat", "a.txt"])
    assert "audit log unavailable" in out["error"] and "alpha" not in json.dumps(out)


def test_extra_folders_appear_on_the_card_and_are_what_gets_granted(env, tmp_path):
    ws, _ = env
    trusted(ws)
    other = tmp_path / "other"
    other.mkdir()
    (other / "n.txt").write_text("nn\n")
    meta, out = run_cmd(binding(ws), "c7", ["cat", str(other / "n.txt")], extra_dirs=[str(other)])
    assert meta["effect"]["extra_dirs"] == [str(other.resolve())] and out["stdout"] == "nn\n"


def test_long_output_is_cut_for_the_model(env):
    ws, _ = env
    trusted(ws)
    (ws / "long.txt").write_text("y" * 40_000)
    _, out = run_cmd(binding(ws), "c8", ["cat", "long.txt"])
    assert out["truncated"] and len(out["stdout"]) < 40_000 and "more characters not shown" in out["stdout"]


# --- there is no other way in -----------------------------------------------------------------------------------


def test_the_command_tools_have_no_http_route():
    pytest.importorskip("mcp")
    from fastapi.testclient import TestClient
    from vivarium_workbench.api import app as appmod
    app = appmod.create_app() if hasattr(appmod, "create_app") else appmod.app
    paths = " ".join(app.openapi()["paths"])
    for needle in ("run_command", "run-command", "workspace_trust", "workspace-trust", "request_workspace"):
        assert needle not in paths
    c = TestClient(app)
    for url in ("/api/run-command", "/api/run_command", "/api/workspace-trust", "/api/request-workspace-trust"):
        assert c.post(url, json={"argv": ["ls"]}).status_code in (404, 405), url


def test_the_tools_exist_only_on_the_token_guarded_mcp_server():
    pytest.importorskip("mcp")
    m = claude_mcp.build()
    names = {t.name for t in go(m.server.list_tools())}
    assert {"run_command", "request_workspace_trust"} <= names
    assert set(chat_commands.TOOL_NAMES) <= names


def test_off_by_default_the_model_never_sees_the_tools():
    off = claude_cli.Attach(mcp_config="{}", allowed=("mcp__workbench__call_operation",),
                            blocked=tuple(claude_mcp.TOOL_PREFIX + n for n in chat_commands.TOOL_NAMES))
    argv = claude_cli.build_argv("claude", "m", "sys", "/tmp/cfg.json", off, None)
    i = argv.index("--disallowedTools")
    assert argv[i + 1] == "mcp__workbench__run_command,mcp__workbench__request_workspace_trust"
    on = claude_cli.Attach(mcp_config="{}", allowed=("a",))
    assert "--disallowedTools" not in claude_cli.build_argv("claude", "m", "sys", "/tmp/cfg.json", on, None)


# --- remembering a fixed read-only inspector for this chat --------------------------------------------------------


def ask_again(b: Binding, call_id: str, argv, decision: Decision = Decision(True), **kw):
    """Like run_cmd, but fails if a card appears when none is expected (returns None for the meta then)."""
    async def main():
        task = asyncio.ensure_future(chat_commands.command_call(b, call_id, argv, kw.get("cwd"), kw.get("extra_dirs")))
        for _ in range(60):
            if call_id in b.coord.pending:
                b.coord.decide({call_id: decision})
                return True, await task
            if task.done():
                return False, task.result()
            await asyncio.sleep(0.01)
        return False, await task
    return go(main())


def test_an_approved_inspector_can_be_remembered_for_the_chat_and_runs_without_a_card_next_time(env):
    ws, _ = env
    trusted(ws)
    b = binding(ws)
    meta, out = run_cmd(b, "m1", ["ls"], Decision(True, remember=True))
    assert meta["effect"]["remember"] is True and "a.txt" in out["stdout"]
    carded, out2 = ask_again(b, "m2", ["ls"])
    assert carded is False and "a.txt" in out2["stdout"], "a remembered inspector must not ask again"
    recs = log_lines(user_state.command_log_path(ws))
    assert [r.get("remembered") for r in recs if r["phase"] == "intent"] == [False, True]      # the second run says why it did not ask
    assert [r["tool_call_id"] for r in recs if r["phase"] == "intent"] == ["m1", "m2"]


def test_only_an_exact_match_is_remembered(env, tmp_path):
    ws, _ = env
    trusted(ws)
    other = tmp_path / "other"
    other.mkdir()
    b = binding(ws)
    run_cmd(b, "e1", ["ls"], Decision(True, remember=True))
    for i, (argv, kw) in enumerate([(["ls", "-l"], {}), (["ls"], {"cwd": "sub"}), (["ls"], {"extra_dirs": [str(other)]}),
                                    (["ls", "sub"], {}), (["pwd"], {})]):
        carded, _ = ask_again(b, f"e{i + 2}", argv, Decision(False), **kw)
        assert carded, f"{argv} {kw} must ask again: it is not the same command"


def test_the_server_not_the_browser_decides_what_may_be_remembered(env):
    ws, _ = env
    trusted(ws)
    b = binding(ws)
    for i, argv in enumerate((["cat", "a.txt"], ["grep", "-n", "beta", "a.txt"], ["find", ".", "-name", "a.txt"])):
        run_cmd(b, f"n{i}", argv, Decision(True, remember=True))      # the browser asked to remember; none of these may be
        carded, _ = ask_again(b, f"n{i}b", argv, Decision(False))
        assert carded, f"{argv} is not a fixed inspector: it must ask every time"
    assert not b.remembered


def test_a_refusal_never_remembers(env):
    ws, _ = env
    trusted(ws)
    b = binding(ws)
    run_cmd(b, "d1", ["ls"], Decision(False, remember=True))
    assert not b.remembered
    carded, _ = ask_again(b, "d2", ["ls"], Decision(False))
    assert carded


def test_remembered_choices_belong_to_one_chat_only(env):
    ws, _ = env
    trusted(ws)
    first = binding(ws)
    run_cmd(first, "c1", ["ls"], Decision(True, remember=True))
    second = binding(ws)                     # another chat (another Claude process, another binding)
    carded, _ = ask_again(second, "c2", ["ls"], Decision(False))
    assert carded and not second.remembered


def test_a_remembered_command_still_needs_trust_and_the_gates(env, monkeypatch):
    ws, _ = env
    trusted(ws)
    b = binding(ws)
    run_cmd(b, "t1", ["ls"], Decision(True, remember=True))
    user_state.revoke_trust(ws)
    assert "not trusted" in go(chat_commands.command_call(b, "t2", ["ls"], None, None))["error"]
    trusted(ws)
    monkeypatch.delenv("VIVARIUM_WORKBENCH_ENABLE_RUN_COMMAND")
    assert "switched off" in go(chat_commands.command_call(b, "t3", ["ls"], None, None))["error"]


def test_a_remembered_command_is_still_revalidated_and_recorded(env, tmp_path):
    ws, _ = env
    trusted(ws)
    outside = tmp_path / "outside"
    outside.mkdir()
    b = binding(ws)
    run_cmd(b, "v1", ["ls", "sub"], Decision(True, remember=True))
    (ws / "sub" / "b.txt").unlink()
    (ws / "sub").rmdir()
    (ws / "sub").symlink_to(outside)         # the folder it names now leads out of the workspace
    out = go(chat_commands.command_call(b, "v2", ["ls", "sub"], None, None))
    assert "error" in out and "stdout" not in out


# --- the decision on the wire -----------------------------------------------------------------------------------


def test_the_decision_shape_with_remember_is_accepted_and_only_the_claude_path_reads_it():
    from vivarium_workbench.lib import ai_chat, ai_claude_code
    from vivarium_workbench.lib.errors import APIError
    raw = {"approvals": {"a": {"approved": True, "remember": True}, "b": True, "c": {"denied": "no"},
                         "d": {"approved": True}}}
    res = ai_chat._deferred_results(raw)
    assert res.approvals["a"] is True and res.approvals["b"] is True and res.approvals["d"] is True
    assert ai_chat._remember_ids(raw) == frozenset({"a"})
    dec = ai_claude_code.decisions_from(res, ai_chat._remember_ids(raw))
    assert dec["a"].remember and dec["a"].approved and not dec["b"].remember and not dec["c"].approved
    for bad in ({"a": {"approved": True, "remember": "yes"}}, {"a": {"approved": False}}, {"a": {"remember": True}}, {"a": 1}):
        with pytest.raises(APIError):
            ai_chat._deferred_results({"approvals": bad})
    assert ai_chat._remember_ids(None) == frozenset() and ai_chat._remember_ids({"approvals": 3}) == frozenset()


def test_a_workspace_cannot_redirect_the_audit_trail_with_a_symlink(env, tmp_path):
    ws, _ = env
    trusted(ws)
    elsewhere = tmp_path / "elsewhere.jsonl"
    elsewhere.write_text("")
    (ws / ".pbg" / "ai-actions.jsonl").symlink_to(elsewhere)
    _, out = run_cmd(binding(ws), "s1", ["cat", "a.txt"])
    assert "symbolic link" in out["error"] and "alpha" not in json.dumps(out)
    assert elsewhere.read_text() == "", "nothing may be written through the link"


def test_a_symlinked_audit_folder_is_refused_too(env, tmp_path):
    ws, _ = env
    trusted(ws)
    (ws / ".pbg").rmdir()
    target = tmp_path / "somewhere"
    target.mkdir()
    (ws / ".pbg").symlink_to(target)
    _, out = run_cmd(binding(ws), "s2", ["cat", "a.txt"])
    assert "symbolic link" in out["error"] and not list(target.iterdir())

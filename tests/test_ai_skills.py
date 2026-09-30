"""Skills in the built-in chat: discovery, safe loading, and the real turn wiring.

Real: the filesystem, the YAML frontmatter parser, the app + middleware + agent loop, the tools.
Stubbed: only the remote LLM (pydantic-ai ``FunctionModel``), as in the other chat suites.
One smoke test reads the REAL installed viva-superpowers plugin and is skipped when it is absent.
"""
import asyncio
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("pydantic_ai")
from fastapi.testclient import TestClient  # noqa: E402
from pydantic_ai import RunContext  # noqa: E402
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart  # noqa: E402
from pydantic_ai.models.function import DeltaToolCall, FunctionModel  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_auth, ai_chat, ai_skills, ai_tools  # noqa: E402


def _skill(root: Path, folder: str, body: str, *, name: str | None = None, desc: str = "does a thing",
           tools: str = "Bash(*) Read") -> Path:
    d = root / folder
    d.mkdir(parents=True)
    fm = f"---\nname: {name or folder}\ndescription: {desc}\nallowed-tools: {tools}\n---\n"
    (d / "SKILL.md").write_text(fm + body)
    return d


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_SKILLS_DIRS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))                 # Path.home() -> an empty home
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    ai_auth._MEMORY.clear()
    ai_auth._SELECTION.clear()
    yield
    ai_auth._MEMORY.clear()
    ai_auth._SELECTION.clear()


# --- discovery ---------------------------------------------------------------------------


def test_discover_reads_frontmatter_and_says_what_each_skill_needs(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    _skill(lib, "status", "Call `curl $URL/api/workspace-manifest` and summarise.")
    _skill(lib, "shipit", "Then run `gh pr create` and `git checkout -b x`.", desc="ships")
    _skill(lib, "runner", "Run `uv run scripts/x.py`.")
    _skill(lib, "editor", "Edit the study.", tools="Bash(*) Read Write Edit")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_SKILLS_DIRS", str(lib))
    found = ai_skills.discover(tmp_path / "ws", local=False)
    assert sorted(found) == ["editor", "runner", "shipit", "status"]
    assert found["shipit"].description == "ships"
    assert found["status"].needs == ()                                   # API only
    assert set(found["shipit"].needs) == {"gh", "git"}
    assert found["runner"].needs == ("shell",)
    assert found["editor"].needs == ("files",)
    s = {x["name"]: x["needs"] for x in ai_skills.summary(found)}
    assert s["status"] == ["no shell, file, git or gh steps detected"] and s["shipit"] == ["gh", "git"]


def test_a_skill_that_probes_the_filesystem_and_git_is_not_called_api_only():
    """Found live: the real viva-status skill walks up to workspace.yaml, TCP-probes, runs git and globs
    studies/*/study.yaml — the first version of the heuristic labelled it 'workbench API only'."""
    body = ("1. Walk up the directory tree from cwd looking for `workspace.yaml`.\n"
            "2. `git rev-parse --abbrev-ref HEAD` and `git status --porcelain`.\n"
            "3. Glob `studies/*/study.yaml` and read each one.\n")
    needs = ai_skills.needs_of(body, {"allowed-tools": "Bash(*) Read"})
    assert {"git", "files"} <= set(needs) and needs != ()


def test_earlier_search_directories_win_and_invalid_names_are_skipped(tmp_path, monkeypatch):
    first, ws = tmp_path / "first", tmp_path / "ws"
    _skill(first, "dup", "FROM FIRST")
    _skill(ws / ".claude" / "skills", "dup", "FROM WORKSPACE")
    _skill(ws / "skills", "only-ws", "x")
    _skill(first, "weird", "x", name="../../etc/passwd")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_SKILLS_DIRS", str(first))
    found = ai_skills.discover(ws, local=False)
    assert "FROM FIRST" in ai_skills.read_skill(found["dup"]) and "only-ws" in found
    assert "../../etc/passwd" not in found and "weird" not in found


def test_a_symlink_out_of_the_search_directory_is_not_followed(tmp_path, monkeypatch):
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: leak\ndescription: x\n---\nTOP SECRET")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "leak").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_SKILLS_DIRS", str(lib))
    assert "leak" not in ai_skills.discover(tmp_path / "ws", local=False)


def test_a_hosted_server_never_reads_the_home_directory_for_skills(tmp_path):
    home = tmp_path / "home"
    plugin = home / "plugins" / "p"
    _skill(plugin / "skills", "homeskill", "x")
    (home / ".claude" / "plugins").mkdir(parents=True)
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"p@m": [{"installPath": str(plugin)}]}}))
    assert "homeskill" in ai_skills.discover(tmp_path / "ws", local=True)          # the user's own machine
    assert "homeskill" not in ai_skills.discover(tmp_path / "ws", local=False)     # a shared/hosted server


def test_one_skill_cannot_fill_the_context(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    _skill(lib, "huge", "x" * (ai_skills.MAX_SKILL_CHARS * 2))
    monkeypatch.setenv("VIVARIUM_WORKBENCH_SKILLS_DIRS", str(lib))
    text = ai_skills.read_skill(ai_skills.discover(tmp_path / "ws", local=False)["huge"])
    assert len(text) < ai_skills.MAX_SKILL_CHARS + 200 and "truncated" in text


# --- the tools ---------------------------------------------------------------------------


def _ctx(app, ws, skills):
    d = ai_tools.ChatDeps(app=app, client=None, ws_root=ws, session_key=None, provider="p", model="m", skills=skills)
    return RunContext(deps=d, model=TestModel(), usage=RunUsage(), tool_call_id="c1")


def test_load_skill_takes_a_name_never_a_path(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    _skill(lib, "ok", "the body")
    (tmp_path / "loot.txt").write_text("LOOT")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_SKILLS_DIRS", str(lib))
    skills = ai_skills.discover(tmp_path / "ws", local=False)
    ctx = _ctx(None, tmp_path, skills)
    good = asyncio.run(ai_tools.load_skill(ctx, "ok"))
    assert good["skill"] == "ok" and "the body" in good["instructions"] and "NO shell" in good["how_to_follow"]
    for evil in ("../loot.txt", "../../etc/passwd", "/etc/passwd", "ok/../ok", ""):
        r = asyncio.run(ai_tools.load_skill(ctx, evil))
        assert "error" in r and r["available"] == ["ok"] and "LOOT" not in json.dumps(r)


# --- prompt + real turn wiring ------------------------------------------------------------


def test_the_prompt_lists_skills_inlines_an_orient_skill_and_manual_gets_none(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    _skill(lib, "acme-orient", "ORIENT-BODY route by state.", desc="orientation")
    _skill(lib, "acme-status", "BODY-STATUS", desc="report the state")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_SKILLS_DIRS", str(lib))
    skills = ai_skills.discover(tmp_path / "ws", local=False)
    agent = ai_chat.build_instructions("agent", skills)
    assert "acme-status: report the state" in agent and "load_skill" in agent
    assert "ORIENT-BODY" in agent and "BODY-STATUS" not in agent          # only the gateway is inlined
    assert "acme-status" in ai_chat.build_instructions("ask", skills)
    assert "acme-status" not in ai_chat.build_instructions("manual", skills)
    assert ai_chat.build_instructions("agent") == ai_chat.build_instructions("agent", {})   # no skills -> unchanged


def _run_turn(tmp_path, monkeypatch, mode, prompt="use the status skill", with_skill=True):
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    if with_skill:
        _skill(lib, "acme-status", "STATUS-STEPS: read the manifest and report.", desc="report the state")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_SKILLS_DIRS", str(lib))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: t\n")
    (ws / ".pbg").mkdir()
    app = appmod.create_app()
    app.state.bind_host = "0.0.0.0"
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    ai_auth.save_credential("anthropic", "sk-ant-abcdefghijklmnopqrstuvwx", None, mode="memory", session="t")
    ai_auth.set_selection("anthropic", "fm", mode="memory", session="t")
    seen: dict = {"tools": None, "results": []}

    def decide(messages, info):
        seen["tools"] = sorted(t.name for t in info.function_tools)
        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart) and p.tool_name == "load_skill":
                    seen["results"].append(p.content)
        if seen["results"]:
            return ("text", "loaded")
        return ("tool", {"name": "acme-status"}) if "load_skill" in seen["tools"] else ("text", "no skill tool")

    def function(messages, info):
        k, v = decide(messages, info)
        return ModelResponse(parts=[TextPart(v)] if k == "text" else [ToolCallPart("load_skill", v)])

    async def stream(messages, info):
        k, v = decide(messages, info)
        if k == "text":
            yield v
        else:
            yield {0: DeltaToolCall(name="load_skill", json_args=json.dumps(v), tool_call_id="s1")}

    monkeypatch.setattr(ai_auth, "build_model",
                        lambda *a, **k: FunctionModel(function, stream_function=stream, model_name="fm"))
    r = TestClient(app).post("/api/chat/turn", json={"messages": [], "prompt": prompt, "mode": mode},
                             headers={"X-VW-Session": "t"})
    frames = [f for f in (json.loads(x) for x in r.text.splitlines()) if f["type"] != "ping"]
    return frames, seen


def test_in_agent_mode_the_model_can_load_a_skill_end_to_end(tmp_path, monkeypatch):
    frames, seen = _run_turn(tmp_path, monkeypatch, "agent")
    assert "load_skill" in seen["tools"] and "list_skills" in seen["tools"]
    assert seen["results"] and "STATUS-STEPS" in json.dumps(seen["results"])
    assert [f["type"] for f in frames][-1] == "done" and not any(f["type"] == "error" for f in frames)


def test_manual_mode_has_no_tools_and_therefore_no_skills(tmp_path, monkeypatch):
    frames, seen = _run_turn(tmp_path, monkeypatch, "manual")
    assert not seen["tools"] and not seen["results"]


def test_no_skills_means_no_skill_tools_are_registered(tmp_path, monkeypatch):
    frames, seen = _run_turn(tmp_path, monkeypatch, "agent", with_skill=False)
    assert "call_operation" in seen["tools"] and "load_skill" not in seen["tools"] and "list_skills" not in seen["tools"]
    assert [f["type"] for f in frames][-1] == "done"


# --- the real plugin (skipped when not installed) --------------------------------------------


def test_the_real_viva_superpowers_plugin_is_discoverable_and_loadable():
    import pwd
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)             # the autouse fixture repointed $HOME
    listing = real_home / ".claude" / "plugins" / "installed_plugins.json"
    if not listing.is_file() or "viva-superpowers" not in listing.read_text():
        pytest.skip("viva-superpowers is not installed")
    import unittest.mock as mock
    with mock.patch.object(Path, "home", classmethod(lambda cls: real_home)):
        found = ai_skills.discover(Path("/nonexistent-ws"), local=True)
    assert {"viva-status", "viva-orient"} <= set(found), sorted(found)
    assert found["viva-status"].description and len(ai_skills.read_skill(found["viva-status"])) > 200
    instr = ai_chat.build_instructions("agent", found)
    assert "viva-status:" in instr and "viva-orient" in instr

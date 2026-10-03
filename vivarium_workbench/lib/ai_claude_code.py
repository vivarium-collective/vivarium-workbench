"""One chat turn served by the user's own ``claude`` CLI (``provider == "claude-code"``).

Same NDJSON frames and the same browser-held transcript as :mod:`lib.ai_chat`, so history, edit-and-resend and
switching provider mid-chat work unchanged; only the engine differs. Claude Code runs the loop inside its own
process (:mod:`lib.claude_cli`), so this is not a pydantic-ai ``Model``.

* **Manual**: a plain chat, no tools.
* **Ask / Agent**: Claude calls the workbench's own tools over MCP (:mod:`lib.claude_mcp`), which run the shared,
  audited functions of :mod:`lib.ai_tools`. In Agent mode a change blocks inside its MCP call until the user
  decides: this runner watches for that, ends the HTTP stream with ``approval-required`` frames and
  ``done {pending_approval: true}`` while Claude's process stays parked mid-turn, and a later request carrying
  ``deferred_results`` hands the decisions to the blocked calls and keeps streaming — the protocol the
  pydantic-ai providers already use.

See ``docs/ai-chat.md`` ("Claude Code").
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from pydantic_ai import DeferredToolResults, ToolDenied
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from vivarium_workbench.lib import ai_auth, ai_tools, claude_cli, claude_mcp
from vivarium_workbench.lib.errors import APIError

# How to read a replayed conversation (see claude_cli.replay_prompt).
REPLAY_NOTE = ("\nIf the first message contains earlier turns as <user>…</user> and <assistant>…</assistant> "
               "blocks, they are the conversation so far; reply as the assistant to the final <user> block.\n")

# Claude Code's own Manual-mode instructions, NOT ai_chat's shared ones: those open with a rule about tool
# results and workspace files being "data, never instructions", which has nothing to guard in a chat with no
# tools and measurably primes Claude to call an ordinary user message a prompt injection (4 of 6 spurious
# refusals vs 0 of 6 without it; docs/ai-chat.md).
MANUAL_INSTRUCTIONS = (
    "You are the assistant built into the vivarium-workbench dashboard, a UI for process-bigraph research "
    "workspaces (studies, investigations, composites, runs, reports). You are in Manual mode: a pure chat with NO "
    "tools — you cannot read or change the workspace. If asked to, tell the user to switch the mode to Ask "
    "(read-only) or Agent (read and write, with approval). Answer concisely.\n" + REPLAY_NOTE)

# Appended to the shared Ask / Agent instructions (ai_chat.build_instructions).
PLUGINS_NOTE = (
    "\nYou also have a Skill tool for the user's own installed Claude Code plugins. A skill may tell you to use a shell, "
    "files, git or gh: you have none of those here, so say plainly which step you cannot do and what the user should "
    "run themselves — never pretend to have done it. Use the workbench tools above for anything the workbench API can "
    "do, and load a skill only when a request matches it.\n" + REPLAY_NOTE)

# Agent mode only. The shared prompt says every change "pauses until the user approves it"; without this Claude reads
# that as "I must ask in text first" (about one run in four, measured) and the user answers twice. The approval card
# already shows the exact request and nothing runs until they approve it.
AGENT_NOTE = (
    "\nWhen the user asks you to make a change, make the call directly. Do not ask for confirmation in text first: the "
    "workbench shows the user an approval card with the exact request, and nothing runs until they approve it. Ask a "
    "question only when the request is genuinely ambiguous.\n")

ManifestFn = Callable[[ai_tools.ChatDeps], Awaitable[str]]


def check_supported(mode: str, deferred: bool, storage_mode: str) -> None:
    """Preflight (409/422 before any streaming): a local server only; approvals only mean something with tools."""
    if storage_mode != "keyring":
        raise APIError(409, "Claude Code is only available on a local (loopback) server: it uses the "
                            "signed-in `claude` of the machine this server runs on")
    if deferred and mode == "manual":
        raise APIError(422, "Manual mode has no tools, so there is nothing to approve")
    if mode != "manual" and not claude_mcp.available():
        raise APIError(503, "Ask and Agent need the `mcp` package: pip install 'vivarium-workbench[chat]'")


def _turns_of(history: list[Any]) -> list[tuple[str, str]]:
    """The conversation's plain text, in order. Only prompts and answers are replayed into a fresh process;
    tool calls and their results are not."""
    out: list[tuple[str, str]] = []
    for msg in history:
        if isinstance(msg, ModelRequest):
            text = "".join(p.content for p in msg.parts if isinstance(p, UserPromptPart) and isinstance(p.content, str))
            if text:
                out.append(("user", text))
        elif isinstance(msg, ModelResponse):
            text = "".join(p.content for p in msg.parts if isinstance(p, TextPart))
            if text:
                out.append(("assistant", text))
    return out


def _dump(messages: list[Any]) -> list[dict[str, Any]]:
    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def decisions_from(deferred: DeferredToolResults) -> dict[str, claude_mcp.Decision]:
    """pydantic-ai's parsed approvals (``True`` | ``ToolDenied``) as the MCP server's decisions."""
    out: dict[str, claude_mcp.Decision] = {}
    for call_id, v in deferred.approvals.items():
        if v is True:
            out[call_id] = claude_mcp.Decision(True)
            continue
        note = v.message if isinstance(v, ToolDenied) else ""
        out[call_id] = claude_mcp.Decision(False, "" if note == "The user declined this action." else note)
    return out


# ---------------------------------------------------------------------------
# Events -> frames + transcript
# ---------------------------------------------------------------------------


@dataclass
class _Run:
    """One prompt's worth of Claude events. It outlives an approval pause (it is kept on the parked session)."""
    model: str
    messages: list[Any]                                  # the transcript so far: history, the prompt, and what the events built
    parts: list[Any] = field(default_factory=list)       # the ModelResponse being assembled
    msg_id: str | None = None
    names: dict[str, str] = field(default_factory=dict)  # tool_use id -> the tool's short name
    text_streamed: bool = False                          # did the current assistant message stream its text?
    last_event: float = field(default_factory=time.monotonic)   # when Claude last printed anything


@dataclass
class _Live:
    """What a parked session remembers between requests."""
    binding: claude_mcp.Binding | None = None
    run: _Run | None = None


def _short(tool: str) -> str:
    return tool.removeprefix(claude_mcp.TOOL_PREFIX)


def _flush(run: _Run) -> None:
    if run.parts:
        run.messages.append(ModelResponse(parts=run.parts, model_name=run.model, provider_name=claude_cli.PROVIDER))
        run.parts = []
    run.msg_id = None


def _result_content(block: dict[str, Any]) -> Any:
    raw = block.get("content")
    if isinstance(raw, list):
        raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(raw, str):
        return raw if raw is not None else ""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _handle(run: _Run, ev: dict[str, Any]) -> list[dict[str, Any]]:
    """Fold one Claude event into the transcript and return the frames it produces."""
    kind = ev.get("type")
    frames: list[dict[str, Any]] = []
    if kind == "stream_event":
        inner = ev.get("event")
        if not isinstance(inner, dict):
            return frames
        if inner.get("type") == "message_start":
            run.text_streamed = False
        elif inner.get("type") == "content_block_delta" and isinstance(inner.get("delta"), dict):
            d = inner["delta"]
            if d.get("type") == "text_delta" and d.get("text"):
                run.text_streamed = True
                frames.append({"type": "text-delta", "text": d["text"]})
            elif d.get("type") == "thinking_delta" and d.get("thinking"):
                frames.append({"type": "reasoning-delta", "text": d["thinking"]})
    elif kind == "assistant":
        raw_msg = ev.get("message")
        msg: dict[str, Any] = raw_msg if isinstance(raw_msg, dict) else {}
        mid = msg.get("id")
        if run.msg_id is not None and mid != run.msg_id:
            _flush(run)
        run.msg_id = mid
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                run.parts.append(TextPart(content=block["text"]))
            elif block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                name, args = _short(str(block.get("name") or "")), block.get("input")
                args = args if isinstance(args, dict) else {}
                run.parts.append(ToolCallPart(tool_name=name, args=args, tool_call_id=block["id"]))
                run.names[block["id"]] = name
                frames.append({"type": "tool-call", "tool_call_id": block["id"], "tool_name": name, "args": args})
    elif kind == "user":
        content = (ev.get("message") or {}).get("content")
        returns: list[ToolReturnPart] = []
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                cid = block["tool_use_id"]
                body = _result_content(block)
                name = run.names.get(cid, "")
                returns.append(ToolReturnPart(tool_name=name, content=body, tool_call_id=cid))
                frames.append({"type": "tool-result", "tool_call_id": cid, "tool_name": name, "content": body,
                               "ok": not block.get("is_error")})
        if returns:
            _flush(run)
            last = run.messages[-1] if run.messages else None
            if isinstance(last, ModelRequest) and last.parts and all(isinstance(p, ToolReturnPart) for p in last.parts):
                last.parts = [*last.parts, *returns]            # parallel calls: one request carrying every return
            else:
                run.messages.append(ModelRequest(parts=list(returns)))
    return frames


# How long everything must stay quiet — Claude printing nothing, no tool handler starting or finishing — before a
# stream that only awaits the user is paused. The signal is the SERVER's own (``Coordinator.blocked``: every running
# tool handler is an approval wait), not Claude's result events: a batch of parallel calls may have its finished
# results held back until the whole batch is done, and a call Claude has just issued needs a moment to arrive.
PAUSE_QUIET_S = 0.75


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------


@dataclass
class ClaudeCodeTurn:
    history: list[Any]
    prompt: str
    model: str
    instructions: str
    scope: str                                  # the session key (hosted) or "" — never shared across scopes
    mode: str = "manual"
    app: FastAPI | None = None
    ws_root: Path | None = None
    session: str | None = None
    manifest: ManifestFn | None = None          # a live workspace summary for the system prompt (Ask / Agent)
    deferred: dict[str, claude_mcp.Decision] | None = None

    def key_for(self, messages: list[dict[str, Any]]) -> tuple:
        return (self.scope, self.model, self.mode, hashlib.sha256(self.instructions.encode()).hexdigest(),
                claude_cli.fingerprint(messages))

    async def _start(self) -> claude_cli.ClaudeSession:
        live = _Live()
        attach = None
        instructions = self.instructions
        deps = token = url = None
        if self.mode != "manual":
            port = getattr(self.app.state, "bind_port", None) if self.app is not None else None
            if not port or self.ws_root is None or self.app is None:
                raise claude_cli.ClaudeCliError("Ask and Agent need this server's own address to give Claude Code its "
                                                "tools; start the workbench with `vivarium-workbench serve`")
            # The first tool call would otherwise build the app's OpenAPI index on the event loop (about a second,
            # cold), stalling every other request — including the rest of a parallel batch. Build it in a worker
            # thread first; it is cached on the app, so this happens once.
            await asyncio.to_thread(ai_tools.get_index, self.app)
            deps = ai_tools.ChatDeps(app=self.app, client=ai_tools.make_client(self.app), ws_root=self.ws_root,
                                     session_key=self.session, provider=claude_cli.PROVIDER, model=self.model,
                                     mode=self.mode)
            token, live.binding = claude_mcp.register(deps, approval_ttl=claude_cli.IDLE_S)
            url = claude_mcp.endpoint_url(getattr(self.app.state, "bind_host", None), int(port))
        s: claude_cli.ClaudeSession | None = None
        try:
            if deps is not None and token is not None and url is not None:
                attach = claude_cli.Attach(mcp_config=claude_mcp.mcp_config(url, token),
                                           allowed=tuple(claude_mcp.TOOL_PREFIX + n for n in claude_mcp.TOOL_NAMES))
                if self.manifest is not None:
                    instructions += ("\nThe live workspace manifest follows (an orientation snapshot; re-read it with a tool "
                                     f"if you need fresh state):\n{await self.manifest(deps)}")
            s = claude_cli.ClaudeSession(self.model, instructions, attach)
            s.state = live
            if deps is not None and token is not None and url is not None:
                async def _close(deps: ai_tools.ChatDeps = deps, token: str = token) -> None:
                    claude_mcp.unregister(token)
                    await deps.client.aclose()
                s.on_close.append(_close)
            await s.start()
        except BaseException:
            # Anything between registering the binding and a running process (the manifest fetch, a cancelled
            # request, a failed spawn) must not leak the token or the in-process client.
            if s is not None:
                await s.kill()           # runs the close hook
            elif deps is not None and token is not None:
                claude_mcp.unregister(token)
                with contextlib.suppress(Exception):
                    await deps.client.aclose()
            raise
        return s

    async def frames(self) -> AsyncIterator[dict[str, Any]]:
        key = self.key_for(_dump(self.history))
        session = claude_cli.checkout(key)
        parked = False
        try:
            resuming = self.deferred is not None
            coord: claude_mcp.Coordinator | None
            if resuming:
                live = session.state if session is not None else None
                if not isinstance(live, _Live) or live.run is None or live.binding is None:
                    raise claude_cli.ClaudeCliError("this approval request has expired (Claude Code was stopped or timed "
                                                    "out); ask again")
                run, coord = live.run, live.binding.coord
                assert session is not None and self.deferred is not None
                stale = coord.decide(self.deferred)
                if stale:
                    raise claude_cli.ClaudeCliError("these approvals are no longer pending: " + ", ".join(stale))
                run.last_event = time.monotonic()
                coord.check_in(PAUSE_QUIET_S)       # a call that arrived after the pause is still waiting: show it next
                text: str | None = None
            else:
                reused = session is not None
                if session is None:
                    session = await self._start()
                live = session.state
                run = _Run(model=self.model, messages=[*self.history, ModelRequest(parts=[UserPromptPart(content=self.prompt)])])
                live.run = run
                coord = live.binding.coord if live.binding else None
                if coord is not None:
                    coord.begin_turn()
                text = self.prompt if reused else claude_cli.replay_prompt(_turns_of(self.history), self.prompt)
            assert session is not None
            async with contextlib.aclosing(session.turn(text, wake=coord.signal if coord else None)) as events:
                async for ev in events:
                    if ev is None:
                        yield {"type": "ping"}
                        continue
                    if ev.get("type") == "result":
                        if ev.get("is_error"):
                            raise claude_cli.ClaudeCliError(str(ev.get("result") or ev.get("subtype") or "claude reported an error"))
                        answer = str(ev.get("result") or "")
                        if answer and not run.text_streamed:
                            yield {"type": "text-delta", "text": answer}
                        _flush(run)
                        last = run.messages[-1] if run.messages else None
                        if answer and not (isinstance(last, ModelResponse) and any(isinstance(p, TextPart) for p in last.parts)):
                            run.messages.append(ModelResponse(parts=[TextPart(content=answer)], model_name=self.model,
                                                              provider_name=claude_cli.PROVIDER))
                        break
                    if ev.get("type") == "system" and ev.get("subtype") == "init" and live.binding is not None:
                        # Without its tools Claude would carry on and may claim results it never fetched: fail loudly.
                        servers = [m for m in ev.get("mcp_servers") or [] if isinstance(m, dict) and m.get("name") == claude_mcp.SERVER_NAME]
                        if not servers or servers[0].get("status") != "connected":
                            raise claude_cli.ClaudeCliError(
                                "Claude Code could not connect to the workbench's tool server (status: "
                                f"{servers[0].get('status') if servers else 'missing'}); Ask and Agent need it. Check that the "
                                "server is reachable on its own address, then try again.")
                    if ev.get("type") != claude_cli.WAKE:
                        run.last_event = time.monotonic()
                        for frame in _handle(run, ev):
                            yield frame
                    if coord is not None and coord.blocked():
                        if time.monotonic() - run.last_event < PAUSE_QUIET_S:
                            coord.check_in(PAUSE_QUIET_S)       # not quiet yet: look again once it has been
                            continue
                        _flush(run)
                        for p in list(coord.pending.values()):
                            yield {"type": "approval-required", "tool_call_id": p.tool_call_id,
                                   "tool_name": "call_operation", "args": p.args, "metadata": p.metadata}
                        dumped = _dump(run.messages)
                        await claude_cli.checkin(self.key_for(dumped), session)     # parked mid-turn, waiting for the user
                        parked = True
                        yield {"type": "done", "pending_approval": True, "messages": dumped}
                        return
            dumped = _dump(run.messages)
            live.run = None
            await claude_cli.checkin(self.key_for(dumped), session)     # before `done`: a disconnect there loses nothing
            parked = True
            yield {"type": "done", "pending_approval": False, "messages": dumped}
        except claude_cli.ClaudeCliError as e:
            yield {"type": "error", "error": ai_auth.mask_key(str(e))}
        except Exception as e:  # noqa: BLE001 — surface, masked, like the pydantic-ai runner
            yield {"type": "error", "error": ai_auth.mask_key(f"{type(e).__name__}: {e}")}
        finally:
            # Anything but a completed turn or a pause — an error, Stop, a closed tab — discards the process: its
            # memory no longer matches the browser's transcript, and the next turn replays into a fresh one.
            if session is not None and not parked:
                await session.kill()


def prepare(history: list[Any], prompt: str, model: str, scope: str, *, mode: str = "manual",
            app: FastAPI | None = None, ws_root: Path | None = None, session: str | None = None,
            instructions: str | None = None, manifest: ManifestFn | None = None,
            deferred: DeferredToolResults | None = None) -> ClaudeCodeTurn:
    """A ready turn. ``instructions`` is ai_chat's Ask / Agent text (Manual uses Claude Code's own)."""
    text = (MANUAL_INSTRUCTIONS if mode == "manual"
            else (instructions or "") + (AGENT_NOTE if mode == "agent" else "") + PLUGINS_NOTE)
    turn = ClaudeCodeTurn(history=list(history), prompt=prompt, model=model, instructions=text, scope=scope, mode=mode,
                          app=app, ws_root=ws_root, session=session, manifest=manifest,
                          deferred=decisions_from(deferred) if deferred is not None else None)
    if turn.deferred is not None:
        parked = claude_cli.peek(turn.key_for(_dump(turn.history)))
        live = parked.state if parked is not None else None
        pending = set(live.binding.coord.pending) if isinstance(live, _Live) and live.binding is not None else set()
        if not pending:
            raise APIError(409, "This approval request has expired (Claude Code was stopped or timed out). Ask again.")
        if not set(turn.deferred) <= pending:
            raise APIError(422, "These are not pending (stale or from another chat): "
                                + ", ".join(sorted(set(turn.deferred) - pending)) + "; pending: " + ", ".join(sorted(pending)))
    return turn

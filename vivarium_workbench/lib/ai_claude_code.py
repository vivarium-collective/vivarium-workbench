"""One chat turn served by the user's own ``claude`` CLI (``provider == "claude-code"``).

Same NDJSON frames and the same browser-held transcript as :mod:`lib.ai_chat`, so history, edit-and-resend
and switching provider mid-chat work unchanged; only the engine differs. Claude Code runs the loop
inside its own process (:mod:`lib.claude_cli`), so this is not a pydantic-ai ``Model``.

Slice 1 is Manual mode only (a pure chat, no tools). See ``docs/ai-chat.md`` ("Claude Code").
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, AsyncIterator

from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from vivarium_workbench.lib import ai_auth, claude_cli
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


def check_supported(mode: str, deferred: bool, storage_mode: str) -> None:
    """Preflight (409/422 before any streaming): loopback-only, Manual-only for now."""
    if storage_mode != "keyring":
        raise APIError(409, "Claude Code is only available on a local (loopback) server: it uses the "
                            "signed-in `claude` of the machine this server runs on")
    if mode != "manual" or deferred:
        raise APIError(422, "Claude Code supports Manual mode (a chat with no tools) so far — switch the mode "
                            "to Manual; Ask and Agent are not available for it yet")


def _turns_of(history: list[Any]) -> list[tuple[str, str]]:
    """The conversation's plain text, in order. Only prompts and answers are replayed; anything else a
    transcript can hold (tool calls/returns from another provider's Agent turns) is not."""
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


def _frame_for(ev: dict[str, Any]) -> list[dict[str, Any]]:
    if ev.get("type") != "stream_event":
        return []
    inner = ev.get("event")
    if not isinstance(inner, dict) or inner.get("type") != "content_block_delta":
        return []
    d = inner.get("delta")
    if not isinstance(d, dict):
        return []
    if d.get("type") == "text_delta" and d.get("text"):
        return [{"type": "text-delta", "text": d["text"]}]
    if d.get("type") == "thinking_delta" and d.get("thinking"):
        return [{"type": "reasoning-delta", "text": d["thinking"]}]
    return []


@dataclass
class ClaudeCodeTurn:
    history: list[Any]
    prompt: str
    model: str
    instructions: str
    scope: str                      # the session key (hosted) or "" — never shared across scopes

    def _key(self, messages: list[dict[str, Any]]) -> tuple:
        return (self.scope, self.model, hashlib.sha256(self.instructions.encode()).hexdigest(),
                claude_cli.fingerprint(messages))

    async def frames(self) -> AsyncIterator[dict[str, Any]]:
        session = claude_cli.checkout(self._key(_dump(self.history)))
        reused = session is not None
        parked = False
        try:
            if session is None:
                session = claude_cli.ClaudeSession(self.model, self.instructions)
                await session.start()
            text = self.prompt if reused else claude_cli.replay_prompt(_turns_of(self.history), self.prompt)
            answer: str | None = None
            streamed = False
            async for ev in session.turn(text):
                if ev is None:
                    yield {"type": "ping"}
                    continue
                for frame in _frame_for(ev):
                    streamed = streamed or frame["type"] == "text-delta"
                    yield frame
                if ev.get("type") == "result":
                    if ev.get("is_error"):
                        raise claude_cli.ClaudeCliError(str(ev.get("result") or ev.get("subtype") or "claude reported an error"))
                    answer = str(ev.get("result") or "")
                    if answer and not streamed:
                        yield {"type": "text-delta", "text": answer}
            messages = [*self.history, ModelRequest(parts=[UserPromptPart(content=self.prompt)]),
                        ModelResponse(parts=[TextPart(content=answer or "")], model_name=self.model,
                                      provider_name=claude_cli.PROVIDER)]
            dumped = _dump(messages)
            await claude_cli.checkin(self._key(dumped), session)     # before `done`: a disconnect there loses nothing
            parked = True
            yield {"type": "done", "pending_approval": False, "messages": dumped}
        except claude_cli.ClaudeCliError as e:
            yield {"type": "error", "error": ai_auth.mask_key(str(e))}
        except Exception as e:  # noqa: BLE001 — surface, masked, like the pydantic-ai runner
            yield {"type": "error", "error": ai_auth.mask_key(f"{type(e).__name__}: {e}")}
        finally:
            # Anything but a completed turn — an error, Stop, a closed tab — discards the process: its memory no
            # longer matches the browser's transcript, and the next turn replays into a fresh one.
            if session is not None and not parked:
                await session.kill()


def prepare(history: list[Any], prompt: str, model: str, scope: str) -> ClaudeCodeTurn:
    return ClaudeCodeTurn(history=list(history), prompt=prompt, model=model,
                          instructions=MANUAL_INSTRUCTIONS, scope=scope)

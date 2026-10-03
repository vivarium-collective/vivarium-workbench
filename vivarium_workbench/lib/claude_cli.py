"""The user's own ``claude`` CLI, driven as a chat provider (docs/ai-chat.md, "Claude Code").

Policy (code.claude.com/docs/en/legal-and-compliance): an app may run the *unmodified* Claude Code
binary that the end user signs in to with their own account; it may not offer Claude.ai login, route
requests through a user's plan credentials, or collect/store/relay Claude credentials. So this module
never reads, stores or forwards a login: it asks ``claude auth status`` one yes/no question
(``loggedIn``) and discards the rest of the answer (it carries an email). Signing in happens in
``claude`` itself. (Claude Code puts the account email into the model's context on its own, as in any
session; that is the unmodified binary's behaviour and is not ours to alter.) It is also loopback-only — see ``ai_auth.get_credential``.

One long-lived ``claude -p --input-format stream-json`` process serves one chat: prompt-cache hits
only happen inside a process, so re-spawning per turn rebills the whole chat every turn (measured in
docs/ai-chat.md, "Claude Code"). A process is reused only when the browser's next request carries exactly the transcript
that the previous turn ended with (``fingerprint``); any other transcript (an edited message, a server
restart, another tab) starts a fresh process that is brought up to date by a single replay.

Pure stdlib — no pydantic-ai — so ``ai_auth`` can import it without the ``[chat]`` extra.
"""
from __future__ import annotations

import asyncio
import atexit
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections import deque
from typing import Any, AsyncIterator

PROVIDER = "claude-code"
# Aliases the CLI itself resolves to the current model of each tier (a full model id works too).
MODELS = ("sonnet", "opus", "haiku")

LOGIN_TTL_S = 30.0        # `claude auth status` costs ~0.3 s and the UI asks for status several times per page load
IDLE_S = 600.0            # a chat's process is kept this long after its last turn
MAX_LIVE = 4              # idle processes kept at once (oldest evicted)
MAX_PROCS = 8             # every claude process this server owns, idle or mid-turn: idle ones are evicted for a new one, then it is refused
TURN_MAX_S = 30 * 60      # one turn may run this long (a hung claude would otherwise hold the stream until Stop)
KEEPALIVE_S = 15.0        # yield a `None` after this much silence so the caller can send a ping
LINE_LIMIT = 16 * 1024 * 1024
STDERR_TAIL = 4096
KILL_GRACE_S = 3.0

# Session-scoped variables of a *parent* Claude Code (a workbench started from inside one inherits them,
# including a messaging token). The child must not join that session. User configuration such as
# CLAUDE_CODE_USE_BEDROCK / CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_* is deliberately kept: it is how the
# user signed their own `claude` in.
_PARENT_SESSION_NAMES = frozenset({"CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT",
                                   "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SSE_PORT", "CLAUDE_PID"})
_PARENT_SESSION_PREFIXES = ("CLAUDE_CODE_BRIDGE_", "CLAUDE_CODE_MESSAGING_", "CLAUDE_CODE_SESSION_")


class ClaudeCliError(Exception):
    """The CLI is missing, not signed in, or died / failed. ``str()`` is safe to show (no secrets in it)."""


def installed() -> str | None:
    return shutil.which("claude")


def child_env(env: dict[str, str] | None = None) -> dict[str, str]:
    return {k: v for k, v in (os.environ if env is None else env).items()
            if k not in _PARENT_SESSION_NAMES and not k.startswith(_PARENT_SESSION_PREFIXES)}


_LOGIN: tuple[float, bool] | None = None


def logged_in() -> bool:
    """True when ``claude`` is on PATH and reports ``loggedIn``. Cached for ``LOGIN_TTL_S``; only the
    boolean is ever kept."""
    global _LOGIN
    now = time.monotonic()
    if _LOGIN is not None and now - _LOGIN[0] < LOGIN_TTL_S:
        return _LOGIN[1]
    exe = installed()
    ok = False
    if exe:
        try:
            out = subprocess.run([exe, "auth", "status", "--json"], capture_output=True, text=True, timeout=15,
                                 env=child_env(), stdin=subprocess.DEVNULL, check=False).stdout
            ok = json.loads(out).get("loggedIn") is True
        except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
            ok = False
    _LOGIN = (now, ok)
    return ok


def forget_login() -> None:
    global _LOGIN
    _LOGIN = None


def build_argv(exe: str, model: str, system_prompt: str) -> list[str]:
    """A plain chat: no built-in tools, no MCP servers, none of the user's settings (so no plugins,
    hooks, skills or CLAUDE.md), nothing written to disk."""
    return [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--tools", "", "--strict-mcp-config", "--setting-sources", "",
            "--disable-slash-commands", "--no-session-persistence", "--model", model,
            "--system-prompt", system_prompt]


def fingerprint(messages: list[dict[str, Any]]) -> str:
    """Identity of a transcript (the JSON-mode dump of its messages)."""
    return hashlib.sha256(json.dumps(messages, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def replay_prompt(turns: list[tuple[str, str]], prompt: str) -> str:
    """The first message to a fresh process: earlier turns as ``<user>``/``<assistant>`` blocks, then the
    new prompt as the final ``<user>`` block (the system prompt tells the model how to read it)."""
    if not turns:
        return prompt
    return "".join(f"<{role}>{_inert(text)}</{role}>\n" for role, text in turns) + f"<user>{_inert(prompt)}</user>"


def _inert(text: str) -> str:
    """A turn's own text must not be able to close its block and forge another speaker."""
    for tag in ("user", "assistant"):
        text = text.replace(f"<{tag}>", f"&lt;{tag}>").replace(f"</{tag}>", f"&lt;/{tag}>")
    return text


class ClaudeSession:
    """One ``claude`` process. ``start()``, then ``turn(text)`` any number of times (one at a time), then
    ``kill()``. The child runs in its own session/process group in an empty temporary directory."""

    def __init__(self, model: str, system_prompt: str):
        self.model = model
        self.system_prompt = system_prompt
        self.proc: asyncio.subprocess.Process | None = None
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._stderr: deque[str] = deque(maxlen=STDERR_TAIL)
        self._drain: asyncio.Task[None] | None = None
        self.timer: asyncio.TimerHandle | None = None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> None:
        exe = installed()
        if not exe:
            raise ClaudeCliError("the `claude` command was not found on PATH — install Claude Code first")
        while len(_LIVE_PIDS) >= MAX_PROCS and _IDLE:     # make room by retiring the longest-idle chat first
            await _IDLE.pop(next(iter(_IDLE))).kill()
        if len(_LIVE_PIDS) >= MAX_PROCS:
            raise ClaudeCliError(f"{MAX_PROCS} Claude Code chats are already running on this server — stop one and retry")
        self._tmp = tempfile.TemporaryDirectory(prefix="vw-claude-")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *build_argv(exe, self.model, self.system_prompt),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=self._tmp.name, env=child_env(), limit=LINE_LIMIT, start_new_session=True)
        except OSError as e:
            self._tmp.cleanup()
            raise ClaudeCliError(f"could not start `claude`: {e}") from None
        _LIVE_PIDS.add(self.proc.pid)
        self._drain = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        with contextlib.suppress(Exception):
            while chunk := await self.proc.stderr.read(1024):
                self._stderr.extend(chunk.decode("utf-8", "replace"))

    def stderr_tail(self) -> str:
        return "".join(self._stderr).strip()[-STDERR_TAIL:]

    async def turn(self, text: str) -> AsyncIterator[dict[str, Any] | None]:
        """Send one user message; yield the CLI's events up to and including its ``result``. Yields ``None``
        after ``KEEPALIVE_S`` of silence. Raises ``ClaudeCliError`` if the process ends first."""
        proc = self.proc
        if proc is None or proc.stdin is None or proc.stdout is None or proc.returncode is not None:
            raise ClaudeCliError("the claude process is not running")
        line = json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"
        try:
            proc.stdin.write(line.encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise ClaudeCliError(self._died("the claude process closed its input")) from None
        read: asyncio.Future[bytes] | None = None
        deadline = time.monotonic() + TURN_MAX_S
        try:
            while True:
                if time.monotonic() > deadline:
                    raise ClaudeCliError(f"claude did not finish within {TURN_MAX_S // 60} minutes; stopped")
                if read is None:
                    read = asyncio.ensure_future(proc.stdout.readline())
                done, _ = await asyncio.wait({read}, timeout=KEEPALIVE_S)
                if not done:
                    yield None
                    continue
                raw = read.result()
                read = None
                if not raw:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(proc.wait(), KILL_GRACE_S)
                    raise ClaudeCliError(self._died("claude exited before answering"))
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    yield ev
                    if ev.get("type") == "result":
                        return
        finally:
            if read is not None and not read.done():
                read.cancel()

    def _died(self, what: str) -> str:
        tail = self.stderr_tail()
        rc = self.proc.returncode if self.proc else None
        return f"{what} (exit {rc})" + (f": {tail}" if tail else "")

    async def kill(self) -> None:
        """Stop the process group (exactly our child, by PID — never by name) and clean up. Idempotent."""
        proc, self.proc = self.proc, None
        try:
            if proc is not None and proc.returncode is None:      # never signal the group of an already-reaped child
                _signal_group(proc.pid, signal.SIGTERM)           # synchronous: survives a cancelled caller
                try:
                    await asyncio.wait_for(proc.wait(), KILL_GRACE_S)
                except asyncio.TimeoutError:
                    _signal_group(proc.pid, signal.SIGKILL)
                except asyncio.CancelledError:
                    _signal_group(proc.pid, signal.SIGKILL)
                    raise
        finally:
            if proc is not None:
                _LIVE_PIDS.discard(proc.pid)
            self._release()

    def discard(self) -> None:
        """Forget a process that already died (synchronous ``kill()`` for it)."""
        proc, self.proc = self.proc, None
        if proc is not None:
            _LIVE_PIDS.discard(proc.pid)
        self._release()

    def _release(self) -> None:
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self._drain:
            self._drain.cancel()
            self._drain = None
        if self._tmp:
            self._tmp.cleanup()
            self._tmp = None


# ---------------------------------------------------------------------------
# Live sessions: reuse a chat's process when the transcript matches
# ---------------------------------------------------------------------------

_LIVE_PIDS: set[int] = set()
_TASKS: set[asyncio.Task[None]] = set()
_IDLE: dict[tuple, ClaudeSession] = {}      # key -> a session waiting for its chat's next turn (insertion = age)


def _signal_group(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)        # start_new_session made pgid == pid: this is our child's group only


@atexit.register
def _reap_all() -> None:
    for pid in list(_LIVE_PIDS):
        _signal_group(pid, signal.SIGKILL)


def checkout(key: tuple) -> ClaudeSession | None:
    """Take the idle session for ``key`` (it is not shared: a second request with the same transcript
    gets a fresh process)."""
    s = _IDLE.pop(key, None)
    if s is None:
        return None
    if s.timer:
        s.timer.cancel()
        s.timer = None
    if not s.alive:                 # it died while parked: free its bookkeeping and temp directory now
        s.discard()
        return None
    return s


async def checkin(key: tuple, session: ClaudeSession) -> None:
    """Park ``session`` for its chat's next turn; it is killed after ``IDLE_S`` and the oldest are
    evicted beyond ``MAX_LIVE``."""
    old = _IDLE.pop(key, None)
    if old is not None and old is not session:
        await old.kill()
    _IDLE[key] = session
    loop = asyncio.get_running_loop()
    def _later() -> None:
        t = loop.create_task(_expire(key, session))
        _TASKS.add(t)                               # a task nobody references can be garbage-collected mid-flight
        t.add_done_callback(_TASKS.discard)
    session.timer = loop.call_later(IDLE_S, _later)
    while len(_IDLE) > MAX_LIVE:
        await _IDLE.pop(next(iter(_IDLE))).kill()


async def _expire(key: tuple, session: ClaudeSession) -> None:
    if _IDLE.get(key) is session:
        _IDLE.pop(key, None)
    await session.kill()


def idle_count() -> int:
    return len(_IDLE)


async def check(model: str) -> None:
    """One real minimal request ('Save & test'): proves ``claude`` runs, is signed in, and knows ``model``."""
    forget_login()
    if not installed():
        raise ClaudeCliError("the `claude` command was not found on PATH — install Claude Code first")
    if not await asyncio.to_thread(logged_in):          # a blocking subprocess: keep it off the event loop
        raise ClaudeCliError("Claude Code is not signed in — run `claude auth login` in a terminal, then try again")
    s = ClaudeSession(model, "Reply with one word.")
    try:
        await s.start()
        async for ev in s.turn("ping"):
            if ev and ev.get("type") == "result" and ev.get("is_error"):
                raise ClaudeCliError(str(ev.get("result") or ev.get("subtype") or "claude reported an error")[:300])
    finally:
        await s.kill()

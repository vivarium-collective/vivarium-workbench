"""The workbench's tools, served to Claude Code over MCP (docs/ai-chat.md, "Claude Code").

In Ask and Agent mode Claude Code runs the agent loop itself, in its own process, so it cannot call the
pydantic-ai tools of :mod:`lib.ai_tools`. Instead the app serves the SAME four tools (``list_operations``,
``describe_operation``, ``call_operation``, ``wait_seconds``) as an MCP server, mounted inside the app at
``MOUNT`` and running on the app's own event loop. Nothing is re-implemented: every handler calls the shared
functions in ``ai_tools`` (exclusion list, path rules, Ask-mode refusal, approval-card metadata, the
intent-first audit and the single-use approval claim).

* **Auth.** Each Claude process gets its own random token (``register``), given to it in its MCP config; the
  mounted app refuses any request without a registered token (``_Auth``), so nothing else on the machine
  can drive the tools, and a token dies with its process.
* **Approval.** A mutating ``call_operation`` does not run: the handler records a pending approval and BLOCKS
  until the browser decides (``Coordinator.ask``). The turn runner (``ai_claude_code``) sees the pending call,
  ends the HTTP stream with an ``approval-required`` frame, and keeps Claude's process parked; the user's
  decision (a later request) resolves the blocked handler, which then runs the call, or reports the denial.
* **Correlation.** Every MCP call carries Claude's own ``tool_use`` id in ``_meta["claudecode/toolUseId"]``,
  the id of the matching event in its output stream; a mutating call without one is refused rather than guessed.

No ``from __future__ import annotations``: the SDK resolves the handlers' type hints at registration.
"""
import asyncio
import contextlib
import importlib.util
import json
import secrets
from collections import Counter
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:    # ai_tools needs pydantic-ai (the chat extra): never import it while the app is merely being imported
    from vivarium_workbench.lib import ai_tools

MOUNT = "/api/ai/mcp"
SERVER_NAME = "workbench"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
TOOL_NAMES = ("list_operations", "describe_operation", "call_operation", "wait_seconds")
TOOL_USE_KEY = "claudecode/toolUseId"       # the _meta key Claude Code adds to every MCP tool call


def available() -> bool:
    """True when the optional ``mcp`` package (the chat extra) is installed."""
    try:
        return importlib.util.find_spec("mcp") is not None
    except (ImportError, ValueError):
        return False


def endpoint_url(bind_host: str | None, port: int) -> str:
    """Where a Claude process on this machine reaches the mounted server (loopback only)."""
    host = "[::1]" if bind_host == "::1" else "127.0.0.1"
    return f"http://{host}:{port}{MOUNT}/"


@dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str = ""          # the user's own note on a refusal
    message: str = ""         # a complete message for the model when the system (not the user) decided: timeout, stop
    remember: bool = False    # the user also asked to remember this approval for the rest of the chat (commands only)


@dataclass
class Pending:
    tool_call_id: str
    args: dict[str, Any]                 # the call as the model sent it
    metadata: dict[str, Any]             # the approval-card payload
    future: "asyncio.Future[Decision]"


class Coordinator:
    """The approval hand-off for one Claude process (one chat). Handlers ``ask``; the turn runner watches
    ``pending`` / ``signal`` and calls ``decide`` when the user answers."""

    def __init__(self) -> None:
        self.pending: dict[str, Pending] = {}
        self.inflight: Counter[str] = Counter()          # every tool handler currently running (reads, waits, approvals)
        self.signal: "asyncio.Queue[None]" = asyncio.Queue()
        self.epoch = ""
        self._check: asyncio.TimerHandle | None = None

    def begin_turn(self) -> None:
        self.epoch = datetime.now(timezone.utc).isoformat()

    @contextlib.asynccontextmanager
    async def running(self, call_id: str) -> AsyncGenerator[None, None]:
        """Bracket one tool handler, so the runner can tell "waiting only on the user" from "still working"."""
        self.inflight[call_id] += 1
        try:
            yield
        finally:
            self.inflight[call_id] -= 1
            if self.inflight[call_id] <= 0:
                del self.inflight[call_id]
            self.signal.put_nowait(None)

    def blocked(self) -> bool:
        """True when something awaits the user and every running handler is one of those waits."""
        return bool(self.pending) and all(k in self.pending for k in self.inflight)

    def check_in(self, delay: float) -> None:
        """Wake the runner after ``delay`` (it re-checks ``blocked`` once things have been quiet that long)."""
        if self._check is not None:
            self._check.cancel()
        self._check = asyncio.get_running_loop().call_later(delay, self.signal.put_nowait, None)

    async def ask(self, tool_call_id: str, args: dict[str, Any], metadata: dict[str, Any], ttl: float) -> Decision:
        if tool_call_id in self.pending:      # one card per id: a second ask must not displace (or inherit) the first
            return Decision(False, message="A change with this id is already waiting for the user's decision; not running "
                                           "a second one. Wait for that answer.")
        fut: "asyncio.Future[Decision]" = asyncio.get_running_loop().create_future()
        entry = self.pending[tool_call_id] = Pending(tool_call_id, args, metadata, fut)
        self.signal.put_nowait(None)
        try:
            return await asyncio.wait_for(fut, ttl)
        except asyncio.TimeoutError:
            return Decision(False, message="The approval request timed out; nothing was done. Ask the user again if it is still wanted.")
        finally:
            if self.pending.get(tool_call_id) is entry:
                del self.pending[tool_call_id]

    def decide(self, decisions: dict[str, Decision]) -> list[str]:
        """Resolve blocked handlers; returns the ids that were not pending (stale or forged)."""
        unknown = []
        for call_id, d in decisions.items():
            p = self.pending.get(call_id)
            if p is None or p.future.done():
                unknown.append(call_id)
            else:
                p.future.set_result(d)
        return unknown

    def cancel_all(self) -> None:
        for p in list(self.pending.values()):
            if not p.future.done():
                p.future.set_result(Decision(False, message="The chat was stopped; nothing was done."))


@dataclass
class Binding:
    """What one Claude process is allowed to reach: fixed when it is started."""
    deps: "ai_tools.ChatDeps"
    coord: Coordinator = field(default_factory=Coordinator)
    approval_ttl: float = 600.0
    # Commands the user approved "for this chat": exact keys (lib/run_command.Plan.key). It lives and dies with this
    # binding, i.e. with the Claude process of one chat; it is never persisted and never shared.
    remembered: set[str] = field(default_factory=set)


BINDINGS: dict[str, Binding] = {}


def register(deps: "ai_tools.ChatDeps", approval_ttl: float) -> tuple[str, Binding]:
    token = secrets.token_urlsafe(32)
    b = BINDINGS[token] = Binding(deps=deps, approval_ttl=approval_ttl)
    return token, b


def unregister(token: str) -> None:
    b = BINDINGS.pop(token, None)
    if b is not None:
        b.coord.cancel_all()


def shutdown() -> None:
    """The app is stopping: answer every blocked approval (so the held-open MCP requests return and the server can
    exit) and refuse anything further."""
    for b in list(BINDINGS.values()):
        b.coord.cancel_all()
    BINDINGS.clear()


def _token_of(header: str) -> str:
    """The bearer token of an ``Authorization`` header, or ``""``: the scheme is required."""
    return header[7:].strip() if header.startswith("Bearer ") else ""


class _Auth:
    """ASGI guard: only a request bearing a registered token reaches the MCP app."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
            if _token_of(auth) not in BINDINGS:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = b'{"error":"unauthorized"}'
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


@dataclass
class Mount:
    server: Any            # the MCPServer (its session_manager must run in the app's lifespan)
    asgi: Any


def _binding_of(ctx: Any) -> Binding:
    req = ctx.request_context.request
    auth = (req.headers.get("authorization", "") if req is not None else "")
    b = BINDINGS.get(_token_of(auth))
    if b is None:
        raise PermissionError("unauthorized")
    return b


def _tool_use_id(ctx: Any) -> str:
    rc = ctx.request_context
    # The SDK hands `_meta` over as a plain dict (stateless mode) or a pydantic object (stateful): accept both,
    # and fall back to the raw request params.
    for meta in (rc.meta, (rc.params or {}).get("_meta")):
        if meta is not None and not isinstance(meta, dict) and hasattr(meta, "model_dump"):
            meta = meta.model_dump()
        v = meta.get(TOOL_USE_KEY) if isinstance(meta, dict) else None
        if isinstance(v, str) and v:
            return v
    return ""


def _call_id(ctx: Any) -> str:
    """A key for one running handler: Claude's tool-use id when the call carries it, else a unique one."""
    return _tool_use_id(ctx) or secrets.token_hex(8)


async def run_call(b: Binding, tool_use_id: str, operation_id: str, path_params: Any, query: Any, body: Any,
                   select: str | None) -> dict[str, Any]:
    """``call_operation`` for Claude Code: the same checks and the same audited execution as the pydantic-ai tool;
    only the approval differs (it blocks in-handler instead of raising ``ApprovalRequired``)."""
    from vivarium_workbench.lib import ai_tools
    deps = b.deps
    path_params, query, body = (ai_tools._decode_json_object(v) for v in (path_params, query, body))
    if any(v is not None and not isinstance(v, dict) for v in (path_params, query, body)):
        return {"error": "path_params, query and body must each be a JSON object (not a string or list)"}
    prepared = ai_tools.prepare_call(deps, operation_id, path_params)
    if isinstance(prepared, dict):
        return prepared
    e, path = prepared
    if e["mutating"]:
        if not tool_use_id:
            return {"error": "this change cannot be tied to an approval card (the call carried no id); not running it"}
        meta = {"operation_id": operation_id, **ai_tools.approval_metadata(e, deps.ws_root, path, query, body)}
        args = {"operation_id": operation_id, "path_params": path_params, "query": query, "body": body}
        d = await b.coord.ask(tool_use_id, args, meta, b.approval_ttl)
        if not d.approved:
            if d.message:
                return {"error": d.message}
            why = d.reason.strip()
            return {"error": "The user declined this action" + (f" (their note: {why})" if why else "") +
                             ". It was NOT done. Do not retry the same call; acknowledge the decision and ask what they "
                             "want instead."}
    return await ai_tools.execute_call(deps, e, path, query, body, select, tool_call_id=tool_use_id, epoch=b.coord.epoch)


def build() -> Mount | None:
    """The MCP server, or ``None`` when the optional ``mcp`` package is not installed (no chat extra)."""
    if not available():
        return None
    from mcp.server.mcpserver import Context, MCPServer
    from mcp.server.transport_security import TransportSecuritySettings

    from vivarium_workbench.lib import ai_tools
    server = MCPServer(SERVER_NAME)

    @server.tool(description=ai_tools.list_operations.__doc__)
    async def list_operations(ctx: Context, tag: str | None = None, query: str | None = None) -> dict[str, Any]:
        b = _binding_of(ctx)
        async with b.coord.running(_call_id(ctx)):
            return ai_tools.list_ops(b.deps, tag, query)

    @server.tool(description=ai_tools.describe_operation.__doc__)
    async def describe_operation(ctx: Context, operation_id: str) -> dict[str, Any]:
        b = _binding_of(ctx)
        async with b.coord.running(_call_id(ctx)):
            return ai_tools.describe_op(b.deps, operation_id)

    @server.tool(description=ai_tools.call_operation.__doc__)
    async def call_operation(ctx: Context, operation_id: str, path_params: dict[str, Any] | str | None = None,
                             query: dict[str, Any] | str | None = None, body: dict[str, Any] | str | None = None,
                             select: str | None = None) -> dict[str, Any]:
        b = _binding_of(ctx)
        use_id = _tool_use_id(ctx)
        async with b.coord.running(use_id or _call_id(ctx)):
            return await run_call(b, use_id, operation_id, path_params, query, body, select)

    @server.tool(description=ai_tools.wait_seconds.__doc__)
    async def wait_seconds(ctx: Context, seconds: float) -> dict[str, Any]:
        b = _binding_of(ctx)
        async with b.coord.running(_call_id(ctx)):
            return await ai_tools.pause(seconds)

    # The command tools (lib/chat_commands.py): registered here and nowhere else, so they have no HTTP route. They are
    # only visible to (and pre-allowed for) a chat when the server was started with --enable-run-command, and every
    # handler checks the same gates again.
    from vivarium_workbench.lib import chat_commands

    @server.tool(description=chat_commands.RUN_DOC)
    async def run_command(ctx: Context, argv: list[str], cwd: str | None = None,
                          extra_dirs: list[str] | None = None) -> dict[str, Any]:
        b = _binding_of(ctx)
        use_id = _tool_use_id(ctx)
        async with b.coord.running(use_id or _call_id(ctx)):
            return await chat_commands.command_call(b, use_id, argv, cwd, extra_dirs)

    @server.tool(description=chat_commands.TRUST_DOC)
    async def request_workspace_trust(ctx: Context) -> dict[str, Any]:
        b = _binding_of(ctx)
        use_id = _tool_use_id(ctx)
        async with b.coord.running(use_id or _call_id(ctx)):
            return await chat_commands.trust_call(b, use_id)

    # Loopback hosts only (DNS-rebinding protection), whatever the SDK would default to.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"], allowed_origins=[])
    app = server.streamable_http_app(streamable_http_path="/", json_response=True, stateless_http=True,
                                     host="127.0.0.1", transport_security=security)
    return Mount(server=server, asgi=_Auth(app))


def mcp_config(url: str, token: str) -> str:
    """The ``--mcp-config`` JSON that points one Claude process at its own binding."""
    return json.dumps({"mcpServers": {SERVER_NAME: {"type": "http", "url": url,
                                                    "headers": {"Authorization": f"Bearer {token}"}}}})

"""Limits on the chat for a server shared with anonymous visitors.

The chat keeps no conversation on the server (the browser holds the transcript), so what a shared server can bound is
(a) how many chat turns run at once and (b) how many model tokens one session id has used. Both apply by default only
where credentials are kept per session in memory (``mode == "memory"``: a non-loopback or proxied server); a private
loopback server is unlimited unless the operator sets the variables.

* ``VIVARIUM_WORKBENCH_CHAT_MAX_TURNS`` - turns running at once in this process (default 4 when shared; ``0`` = no limit).
* ``VIVARIUM_WORKBENCH_CHAT_SESSION_TOKENS`` - model tokens one session id may use (default 500000 when shared; ``0`` = no limit).

The session id is a routing key the client supplies, not a login, so the token budget bounds an ordinary client and not
one that rotates its id; the turn limit is what bounds the server itself.
"""
from __future__ import annotations

import threading
import time
import weakref
from collections import OrderedDict
from typing import Any, AsyncIterator, Callable

from vivarium_workbench.lib.env_compat import get_env
from vivarium_workbench.lib.errors import APIError

DEFAULT_TURNS = 4
DEFAULT_SESSION_TOKENS = 500_000
LEASE_S = 3600.0          # a claimed turn slot that was never released (its stream was dropped before it started) expires
MAX_SESSIONS = 5000       # the token ledger forgets the least recently used session beyond this

_LOCK = threading.Lock()
_ACTIVE: dict[object, float] = {}
_USED: "OrderedDict[str, int]" = OrderedDict()


def _setting(name: str, shared_default: int, shared: bool) -> int:
    raw = (get_env(name, "") or "").strip()
    if raw:
        try:
            if int(raw) >= 0:
                return int(raw)
        except ValueError:
            pass              # an unreadable or negative value falls back to the default rather than lifting the limit
    return shared_default if shared else 0


def max_turns(shared: bool) -> int:
    return _setting("CHAT_MAX_TURNS", DEFAULT_TURNS, shared)


def session_token_limit(shared: bool) -> int:
    return _setting("CHAT_SESSION_TOKENS", DEFAULT_SESSION_TOKENS, shared)


def claim_turn(shared: bool) -> Callable[[], None]:
    """Take one of the process's turn slots, or raise ``APIError(429)``. Call the returned function when the turn ends."""
    cap = max_turns(shared)
    if cap == 0:
        return lambda: None
    now = time.monotonic()
    with _LOCK:
        for slot, since in list(_ACTIVE.items()):
            if now - since > LEASE_S:
                del _ACTIVE[slot]
        if len(_ACTIVE) >= cap:
            raise APIError(429, f"This server is already running {cap} chat turns; try again in a moment.")
        slot = object()
        _ACTIVE[slot] = now

    def release() -> None:
        with _LOCK:
            _ACTIVE.pop(slot, None)
    return release


def tokens_left(session: str | None, shared: bool) -> int | None:
    """Tokens this session may still use, ``None`` when there is no limit; raises ``APIError(429)`` when it is spent."""
    cap = session_token_limit(shared)
    if cap == 0:
        return None
    with _LOCK:
        left = cap - _USED.get(session or "", 0)
    if left <= 0:
        raise APIError(429, f"This session has used its {cap} token limit; start a new conversation in a new browser "
                            "tab or ask the operator to raise VIVARIUM_WORKBENCH_CHAT_SESSION_TOKENS.")
    return left


def spend(session: str | None, tokens: int, shared: bool) -> None:
    """Record the tokens a finished run used (only when a limit applies, so a private server keeps no ledger)."""
    if tokens <= 0 or session_token_limit(shared) == 0:
        return
    key = session or ""
    with _LOCK:
        _USED[key] = _USED.pop(key, 0) + tokens
        while len(_USED) > MAX_SESSIONS:
            _USED.popitem(last=False)


class Held:
    """An async iterator over a turn's frames that gives the turn slot back however the stream ends: when it is
    exhausted, when it fails or is cancelled, when it is closed, and when it is dropped without ever being started
    (a client that disconnects before the first read; an unstarted async generator would never run its ``finally``)."""

    def __init__(self, frames: AsyncIterator[Any], release: Callable[[], None]) -> None:
        self._frames, self._release = frames.__aiter__(), release
        weakref.finalize(self, release)           # release is idempotent and does not refer back to self

    def __aiter__(self) -> "Held":
        return self

    async def __anext__(self) -> Any:
        try:
            return await self._frames.__anext__()
        except BaseException:                     # includes StopAsyncIteration (done) and CancelledError
            self._release()
            raise

    async def aclose(self) -> None:
        try:
            close = getattr(self._frames, "aclose", None)
            if close is not None:
                await close()
        finally:
            self._release()

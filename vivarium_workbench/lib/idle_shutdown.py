"""Idle self-shutdown for a locally-served workbench.

A detached ``vivarium-workbench serve`` is designed to outlive the shell that
launched it, so the browser dashboard keeps working after the skill/session that
started it returns. The cost is that nothing ever reaps it: across many sessions
these detached servers accumulate and leak gigabytes of RAM (and occasionally
spin a core at 100%).

This module lets a server reap *itself* once nobody is using it. Every HTTP
request stamps a "last activity" time; a daemon thread shuts the server down
after a configurable idle window with no requests. An actively-viewed dashboard
keeps the timer reset — the SPA polls on an interval even when the tab is
backgrounded — so it stays up; an abandoned one exits on its own.

**Loopback-only by default.** A server bound to ``0.0.0.0`` or served behind a
base path is a long-lived deployment (container / ALB), not an ephemeral local
session, so the idle window does *not* apply there unless asked for explicitly
via ``--idle-timeout`` / ``VIVARIUM_WORKBENCH_IDLE_TIMEOUT``.

The decision logic (:func:`should_shutdown`, :func:`resolve_idle_seconds`) is
pure and unit-tested; the thread and signal plumbing are thin wrappers over it.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from typing import Callable, Mapping, Optional

# The local default idle window, in minutes. Generous enough that an open
# browser tab's polling always resets it well within the window; short enough
# that an abandoned server is gone within the hour.
DEFAULT_IDLE_MINUTES = 90.0

# How often the watchdog wakes to check (seconds). Coarse on purpose — this is a
# janitor, not a deadline.
_CHECK_INTERVAL_S = 30.0

_ENV_VAR = "VIVARIUM_WORKBENCH_IDLE_TIMEOUT"  # minutes; "0" disables

_lock = threading.Lock()
_last_activity = time.monotonic()


def touch() -> None:
    """Record that a request just arrived (called from the request middleware)."""
    global _last_activity
    with _lock:
        _last_activity = time.monotonic()


def seconds_idle(now: Optional[float] = None) -> float:
    """Seconds since the last :func:`touch` (monotonic clock)."""
    with _lock:
        last = _last_activity
    return (time.monotonic() if now is None else now) - last


def should_shutdown(last_activity: float, now: float, limit_s: float) -> bool:
    """Pure decision: has the server been idle for at least ``limit_s`` seconds?

    ``limit_s <= 0`` disables idle shutdown (always ``False``).
    """
    if limit_s <= 0:
        return False
    return (now - last_activity) >= limit_s


def resolve_idle_seconds(
    arg_minutes: Optional[float],
    *,
    host: str,
    base_path: str,
    env: Optional[Mapping[str, str]] = None,
) -> float:
    """Resolve the idle window in **seconds** from arg > env > zero-config default.

    - An explicit ``arg_minutes`` (CLI ``--idle-timeout``) always wins; ``0``
      disables.
    - Else the ``VIVARIUM_WORKBENCH_IDLE_TIMEOUT`` env var (minutes; ``0``
      disables) applies.
    - Else the zero-config default (:data:`DEFAULT_IDLE_MINUTES`) applies **only**
      to an ephemeral loopback server; a non-loopback bind or a base path
      (container / proxied deployment) stays up forever (``0.0``).
    """
    env = os.environ if env is None else env

    if arg_minutes is not None:
        return max(0.0, float(arg_minutes) * 60.0)

    raw = env.get(_ENV_VAR)
    if raw is not None and raw.strip() != "":
        try:
            return max(0.0, float(raw) * 60.0)
        except ValueError:
            pass  # malformed env → fall through to the default

    loopback = host in ("127.0.0.1", "localhost", "::1", "")
    if loopback and not base_path:
        return DEFAULT_IDLE_MINUTES * 60.0
    return 0.0


def _default_on_idle() -> None:
    """Graceful shutdown: SIGTERM ourselves so uvicorn drains and ``atexit``
    unregisters the server from the running-server registry + pid file."""
    os.kill(os.getpid(), signal.SIGTERM)


def start_idle_watchdog(
    limit_s: float,
    *,
    on_idle: Optional[Callable[[], None]] = None,
    check_interval_s: float = _CHECK_INTERVAL_S,
) -> Optional[threading.Thread]:
    """Start a daemon thread that fires ``on_idle`` once the server has been idle
    for ``limit_s`` seconds. No-op (returns ``None``) when ``limit_s <= 0``.

    The default ``on_idle`` sends ``SIGTERM`` to this process, which uvicorn
    turns into a graceful shutdown.
    """
    if limit_s <= 0:
        return None

    touch()  # arm from "now" so a freshly booted, as-yet-unvisited server gets a full window
    action = on_idle or _default_on_idle

    def _run() -> None:
        while True:
            time.sleep(check_interval_s)
            if should_shutdown(_last_activity_ts(), time.monotonic(), limit_s):
                action()
                return

    thread = threading.Thread(target=_run, name="workbench-idle-watchdog", daemon=True)
    thread.start()
    return thread


def _last_activity_ts() -> float:
    """Internal: the raw last-activity timestamp (monotonic), lock-guarded."""
    with _lock:
        return _last_activity

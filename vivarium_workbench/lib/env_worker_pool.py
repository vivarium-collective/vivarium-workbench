"""Warm env-worker pool — the `EnvironmentResolver`'s worker lifecycle
(env-worker-protocol.md §17).

Keeps warm env workers keyed by `(workspace, interpreter)` so a session's repeated
env queries **reuse one worker** — paying `build_core` once, not per request. This
is not an optimization: `build_core` is ~15 s on v2ecoli (measured), so a
spawn-per-query design would put ~15 s on every composite / registry request. The
pool is what makes the route migrations viable.

Policy (protocol §17): **lazy spawn** (a worker starts on first use), **idle-TTL
eviction** (a worker idle past `T_idle` is reaped), and a **global LRU cap** `K`
(admitting the K+1-th worker evicts the least-recently-used). `T_idle` and `K` are
runtime config. Eviction frees the **process**; the venv (workspace-store §8) and
its GC are separate.

**Slice scope:** the pool + eviction, standalone and tested. It is not yet wired
into `WorkspaceContext` / the routes (that's the next slices), so this is additive
and behavior-preserving. Keying is `(workspace, interpreter)` today; it becomes the
environment coordinate once materialization lands.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from vivarium_workbench.lib.env_compat import get_env
from vivarium_workbench.lib.env_worker_client import EnvWorker, EnvWorkerUnavailable


def _int_env(name: str, default: int) -> int:
    try:
        v = get_env(name, str(default))
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _safe_close(worker: EnvWorker) -> None:
    try:
        worker.close()
    except Exception:  # noqa: BLE001 — eviction must never raise
        pass


class _Entry:
    __slots__ = ("worker", "last_used")

    def __init__(self, worker: EnvWorker):
        self.worker = worker
        self.last_used = time.monotonic()


class WorkerPool:
    """A bounded pool of warm env workers keyed by ``(workspace, interpreter)``."""

    def __init__(self, *, max_workers: int | None = None, idle_ttl: float | None = None):
        # K and T_idle (seconds), config-overridable (plan §G).
        self.max_workers = max_workers if max_workers is not None else _int_env("ENV_WORKER_POOL_MAX", 8)
        self.idle_ttl = idle_ttl if idle_ttl is not None else _int_env("ENV_WORKER_IDLE_TTL", 900)
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._lock = threading.Lock()

    # -- public -------------------------------------------------------------
    def call(self, workspace, method: str, params: dict | None = None,
             *, interpreter: str | None = None) -> dict:
        """Query the warm worker for this environment. On a worker that died or
        was evicted mid-flight, drop it and respawn once (protocol §9)."""
        ws, interp = str(Path(workspace)), interpreter or sys.executable
        try:
            return self._acquire(ws, interp).call(method, params)
        except EnvWorkerUnavailable:
            self._drop(ws, interp)
            return self._acquire(ws, interp).call(method, params)

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def discard(self, workspace, *, interpreter: str | None = None) -> None:
        """Evict this environment's worker (e.g. on a session switch)."""
        self._drop(str(Path(workspace)), interpreter or sys.executable)

    def close_all(self) -> None:
        with self._lock:
            workers = [e.worker for e in self._entries.values()]
            self._entries.clear()
        for w in workers:
            _safe_close(w)

    # -- internals ----------------------------------------------------------
    def _acquire(self, ws: str, interp: str) -> EnvWorker:
        key = (ws, interp)
        to_close: list[EnvWorker] = []
        with self._lock:
            to_close.extend(self._reap_idle_locked())
            entry = self._entries.get(key)
            if entry is not None and entry.worker.alive():
                entry.last_used = time.monotonic()
                worker = entry.worker
            else:
                if entry is not None:                      # a dead worker under this key
                    to_close.append(self._entries.pop(key).worker)
                while len(self._entries) >= self.max_workers and self._entries:
                    to_close.append(self._pop_lru_locked())  # LRU cap (protocol §17)
                worker = EnvWorker(ws, interpreter=interp)   # lazy spawn (Popen is ~ms; build_core is on first call)
                self._entries[key] = _Entry(worker)
        for w in to_close:
            _safe_close(w)
        return worker

    def _drop(self, ws: str, interp: str) -> None:
        with self._lock:
            entry = self._entries.pop((ws, interp), None)
        if entry is not None:
            _safe_close(entry.worker)

    def _reap_idle_locked(self) -> list[EnvWorker]:
        now = time.monotonic()
        stale = [k for k, e in self._entries.items() if now - e.last_used > self.idle_ttl]
        return [self._entries.pop(k).worker for k in stale]

    def _pop_lru_locked(self) -> EnvWorker:
        lru_key = min(self._entries, key=lambda k: self._entries[k].last_used)
        return self._entries.pop(lru_key).worker


# ---------------------------------------------------------------------------
# Process-wide singleton (a later slice binds per-session workers through this).
# ---------------------------------------------------------------------------
_pool: WorkerPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> WorkerPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = WorkerPool()
    return _pool

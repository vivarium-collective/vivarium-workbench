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

import logging
import threading
import time
from pathlib import Path
from typing import Any, Protocol, cast, TYPE_CHECKING

from vivarium_workbench.lib.env_compat import get_env
from vivarium_workbench.lib.env_worker_provision import install_specs_from_workspace

logger = logging.getLogger(__name__)
from vivarium_workbench.lib.env_worker_client import EnvWorker, EnvWorkerUnavailable
from vivarium_workbench.lib.env_worker_routing import is_job_class

if TYPE_CHECKING:  # launchers import the pool's callers; keep it type-only
    from vivarium_workbench.lib.env_worker_launcher import WorkerLauncher


def _int_env(name: str, default: int) -> int:
    try:
        v = get_env(name, str(default))
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


class _TaskCapable(Protocol):
    """A worker whose transport can record a call as a durable task.

    Only the proxy qualifies: viva-api owns the socket there, so it can keep
    working after the client stops waiting and can write down what happened. A
    local subprocess and a dial-back worker are both held by THIS process, so a
    task record would have nowhere to survive.

    Expressed as a protocol rather than an isinstance check on ProxyEnvWorker so
    the pool keeps knowing nothing about transports -- which is the property that
    let a third one be added without touching this file.
    """

    supports_tasks: bool

    def call_task(self, method: str, params: dict | None = None) -> Any: ...


def _task_capable(worker: object) -> "_TaskCapable | None":
    """The worker, narrowed, if it can run tasks — else None.

    The attribute is checked rather than assumed because `WorkerLauncher.launch`
    is typed as returning an `EnvWorker`, and two of the three transports return
    exactly that.
    """
    if getattr(worker, "supports_tasks", False) and callable(getattr(worker, "call_task", None)):
        return cast("_TaskCapable", worker)
    return None


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

    def __init__(self, *, max_workers: int | None = None, idle_ttl: float | None = None,
                 call_timeout: float | None = None, launcher=None):
        # HOW a worker is created is the launcher's business; the pool owns only
        # WHEN (lazy spawn, idle-TTL eviction, LRU cap). Local subprocess or
        # remote image-as-worker is ONE deployment-wide decision made at the
        # composition root — see env_worker_launcher.default_launcher (§2A.8).
        # `launcher`, when given, overrides it (tests, and any caller that wants
        # one explicit transport).
        self._launcher: WorkerLauncher | None = launcher
        self._deployment_launcher: WorkerLauncher | None = None
        self._warned: set[tuple[str, str]] = set()
        # K and T_idle (seconds), config-overridable (plan §G).
        self.max_workers = max_workers if max_workers is not None else _int_env("ENV_WORKER_POOL_MAX", 8)
        self.idle_ttl = idle_ttl if idle_ttl is not None else _int_env("ENV_WORKER_IDLE_TTL", 900)
        # Per-call socket timeout (seconds). 60s suits interactive calls, but a
        # long baseline (e.g. a multi-generation ecoli_baseline, default 2700
        # steps, ~minutes) dispatched through the pool would exceed it and trip
        # EnvWorkerUnavailable → one respawn → fail. Config-overridable so such
        # workloads can raise it; default unchanged (backward-compatible).
        self.call_timeout = call_timeout if call_timeout is not None else _int_env("ENV_WORKER_CALL_TIMEOUT", 60)
        self._entries: dict[tuple[str, str, str], _Entry] = {}
        self._lock = threading.Lock()

    # -- public -------------------------------------------------------------
    def call(self, workspace, method: str, params: dict | None = None,
             *, interpreter: str | None = None) -> dict:
        """Query the warm worker for this environment. On a worker that died or
        was evicted mid-flight, drop it and respawn once (protocol §9).

        When ``interpreter`` is not given, the LAUNCHER names the environment
        (``env_key``): the workspace's own interpreter for a local worker, the
        image's commit for a remote one. Order matters — resolving an interpreter
        first asks a venv-less workspace a question the remote path never needed
        answered, and under REQUIRE_WORKSPACE_VENV that raises instead of routing.
        """
        ws = str(Path(workspace))
        launcher = self._launcher_for(method, ws)
        interp = interpreter or launcher.env_key(ws)
        worker = self._acquire(ws, interp, launcher)

        # Plan §E option (e), step 5. A job-class method runs a study to
        # completion; holding a socket open for that is what the double-run bug
        # was made of. Where the transport can record the call durably -- only
        # the proxy can, because only there does viva-api own the socket -- run
        # it as a TASK instead: the caller still waits, but on short status
        # reads rather than one socket held for hours, so the timeout that used
        # to fire mid-study has nothing to fire on.
        #
        # Not a fallback and not a retry. If this path is available it is the
        # only path taken; step 1's refusal still guards the transports that
        # have no task tier (a local subprocess and a dial-back worker are both
        # held by THIS process, so there is nowhere durable to put the record).
        task_worker = _task_capable(worker)
        if is_job_class(method) and task_worker is not None:
            return task_worker.call_task(method, params)

        try:
            return worker.call(method, params)
        except EnvWorkerUnavailable:
            # Respawn-and-retry is protocol §9 for a worker that died or was
            # evicted mid-flight, and it is right for an interactive call: the
            # work was a query, it did not happen, doing it again is free.
            #
            # It is WRONG for a job-class method, and was silently running every
            # real study twice. ENV_WORKER_CALL_TIMEOUT is a SOCKET timeout
            # (env_worker_client.py: `sock.settimeout`), not a deadline with
            # cancellation -- nothing tells the worker to stop. `_run_study`
            # runs a study's baseline and every declared variant to completion
            # through blocking subprocesses, so it exceeds 60s as a matter of
            # course; the timeout fired, this line re-ran the whole study, and
            # the FIRST run carried on writing to the same runs.db. Two
            # concurrent simulations, one of them orphaned and unattributable.
            #
            # A job-class failure is therefore reported, never repeated. The
            # caller learns the truth -- the work may well still be running --
            # instead of being handed a second copy of it.
            if is_job_class(method):
                raise EnvWorkerUnavailable(
                    f"{method} lost its worker connection. It was NOT retried: "
                    f"this call may still be running in the worker, and running "
                    f"it again would duplicate its writes. Check the study's "
                    f"runs.db before resubmitting."
                ) from None
            self._drop(ws, interp, launcher.kind)
            return self._acquire(ws, interp, launcher).call(method, params)

    def _launcher_for(self, method: str, ws: str):
        """EVERY method runs in the active context's own environment.

        Transport is not a per-method choice. `env_worker_launcher` states the rule
        this restores -- "selected by deployment topology, not preference" -- and an
        earlier version of this method broke it by pinning job-class methods to a
        local subprocess even on a hosted deployment.

        **Why that was wrong, and not merely badly tuned.** "Local" meant "a
        subprocess inside the workbench pod". That was trivially right when one
        workbench process was bound to one workspace, and stopped being right when
        contexts became switchable. A session now gets its own exclusive workspace
        dir (`materialize_session_build`) stamped with one `(simulator_id, commit)`,
        so the active context has BOTH halves of an environment: its data is that
        dir on the PVC, and its computation is the worker-as-image for that commit.
        Those are two halves of one thing, which is exactly what this pool keys on.
        A subprocess in the workbench pod is not "local" to that context at all --
        it is a *different* environment that happened to be nearby.

        Concretely it could not work: the pod's own interpreter cannot import the
        workspace package (#932's slim image), and the 0.3.57 bridge that let a
        venv-less build borrow the base workspace's venv died when the base became
        a scaffold. Every job-class call on a hosted deployment raised "workspace
        has no .venv" -- advice pointing at the very thing §2A.8 removed.

        Running job-class work in the build's own image is not a compromise; it is
        *more* correct than the subprocess ever was, because it is the same image
        the simulation itself runs in.

        What genuinely does not belong in a worker is work too large for one --
        1000 seeds x 10 generations. That is a question about SCALE, not about
        method identity or transport, and it dispatches to viva-api rather than
        choosing a different launcher. `is_job_class` marks the candidates for that
        precheck (step 2); until it exists this warns once per (method, workspace)
        so the gap stays discoverable.
        """
        if self._launcher is not None:
            return self._launcher
        if self._deployment_launcher is None:
            from vivarium_workbench.lib.env_worker_launcher import default_launcher
            self._deployment_launcher = default_launcher()
        launcher = self._deployment_launcher
        # Warn only where the budget is real. "Sized for interaction" is a property
        # of a hosted worker POD (1 CPU / 2 GiB); a laptop subprocess has no such
        # ceiling, and running a study there is the ordinary, expected path — so
        # warning on it would be noise telling the user to dispatch work that has
        # nowhere better to go. Once per (method, workspace).
        if (getattr(launcher, "kind", "local") == "remote"
                and is_job_class(method) and (method, ws) not in self._warned):
            self._warned.add((method, ws))
            logger.warning(
                "%s runs in this context's env worker, which is sized for "
                "interaction. Work at deployment scale should dispatch to viva-api "
                "instead (see remote_pinned.resolve_run_target / "
                "remote_run_views.remote_run_submit); the scale precheck that would "
                "decide automatically is REFACTOR-PLAN §2A.8 workstream 8 step 2.",
                method)
        return launcher

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def discard(self, workspace, *, interpreter: str | None = None) -> None:
        """Evict this environment's worker(s) (e.g. on a session switch).

        Drops every worker for the workspace, whatever its environment key: methods
        route per class, so one workspace can hold a remote worker (interactive) and
        a local one (job-class) at once, and a switch must not leave either behind.

        Matching on the workspace alone — rather than reconstructing the key — is
        what makes that reliable. The keys are the launchers' to mint (a resolved
        interpreter, an image commit); guessing one here missed them and quietly
        left warm workers pinned to the old session.
        """
        ws = str(Path(workspace))
        with self._lock:
            keys = [k for k in self._entries
                    if k[0] == ws and (interpreter is None or k[1] == interpreter)]
        for key in keys:
            self._drop(*key)

    def close_all(self) -> None:
        with self._lock:
            workers = [e.worker for e in self._entries.values()]
            self._entries.clear()
        for w in workers:
            _safe_close(w)

    # -- internals ----------------------------------------------------------
    def _acquire(self, ws: str, interp: str, launcher) -> EnvWorker:
        # Keyed by kind as well: a local and a remote worker for the same
        # (workspace, interpreter) are DIFFERENT environments and must never
        # share a pool entry.
        key = (ws, interp, launcher.kind)
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
                # lazy spawn (local: Popen is ~ms; remote: a pod dialling back —
                # build_core is on the first call either way)
                worker = launcher.launch(ws, interpreter=interp, timeout=self.call_timeout)
                # Provision catalog-installed modules into the fresh worker BEFORE
                # it is cached/handed out, so no caller ever sees an unprovisioned
                # worker (v1: synchronous under the lock — acceptable for the
                # low-concurrency per-session workbench; a failure leaves the
                # worker usable, just without those modules).
                self._provision_worker(ws, worker)
                self._entries[key] = _Entry(worker)
        for w in to_close:
            _safe_close(w)
        return worker

    def _provision_worker(self, ws: str, worker: EnvWorker) -> None:
        """Push the workspace's declared module install specs to a fresh worker.

        Best-effort and never raises: a provisioning failure leaves the worker
        fully usable (composites needing those modules simply won't register,
        exactly as before this feature). Modules the user installed through the
        Catalog tab land in the workbench PVC, which the worker cannot see; this
        is how they reach the worker's environment.
        """
        try:
            specs = install_specs_from_workspace(ws)
        except Exception:  # noqa: BLE001
            return
        if not specs:
            return
        try:
            res = worker.call("install_modules", {"modules": specs})
            failed = [r for r in (res or {}).get("results", []) if not r.get("ok")]
            if failed:
                logger.warning("env-worker provisioning: %d module(s) failed: %s",
                               len(failed), ", ".join(f"{r['name']} ({r['detail']})" for r in failed))
        except Exception as e:  # noqa: BLE001
            logger.warning("env-worker provisioning call failed (worker still usable): %s", e)

    def _drop(self, ws: str, interp: str, kind: str = "local") -> None:
        with self._lock:
            entry = self._entries.pop((ws, interp, kind), None)
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
                # Graceful shutdown on process exit. (Workers also self-terminate
                # when the parent's socket EOFs, so a hard kill still leaks nothing.)
                import atexit
                atexit.register(_pool.close_all)
    return _pool

"""Async materialization jobs (``docs/materialization-lifecycle.md`` §9c).

Runs the synchronous ``materialize`` primitive (§9b) **out-of-band** with progress
+ terminal state, so a ``bind``/``switch`` to a not-yet-materialized source can
return immediately (``MATERIALIZING``) instead of blocking the HTTP worker on a
minutes-scale ``uv sync`` — the worker never runs it inline (§3), and the query
path's 60 s socket timeout would kill it anyway (§1).

**This slice:** the in-process job registry — a background thread per environment
coordinate, **deduplicated** (§5: five sessions on one coordinate share one job,
not five ``uv sync``s), with coarse progress (``queued → syncing → ready | failed``)
and status polling. A cached venv is ``ready`` immediately (fast path). A failed
job is retried only on an explicit ``start`` (§4: FAILED is not retried in a loop).

**Deferred to §9d:** a detached *process* + durable record surviving restart (§7),
restart reconcile (in-flight → FAILED on a dead process), a ``uv sync`` concurrency
cap (§10), and GC. Progress here is in-memory — the same ephemeral tier as
``run_jobs.py``'s job manager (lost on restart); the durable artifact is the
coordinate-keyed venv on disk.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from vivarium_workbench.lib.materialization import (
    MaterializationError,
    cached_interpreter,
    environment_coordinate,
    materialize,
)

# Status values (session-registry §5 maps these onto MATERIALIZING/READY/FAILED).
QUEUED = "queued"
CLONING = "cloning"
SYNCING = "syncing"
READY = "ready"
FAILED = "failed"

_ACTIVE = (QUEUED, CLONING, SYNCING)


def _managed_key(repo: str, ref: str) -> str:
    """Stable dedup key for a managed ``(repo, ref)`` job."""
    import hashlib
    return hashlib.sha256(f"{repo}\0{ref}".encode("utf-8")).hexdigest()[:16]


class MaterializationJob:
    """One environment's materialization — its live state, mutated by the worker
    thread and read by status polls. Fields are simple attributes; under the GIL a
    single-assignment transition is observed atomically, and transitions are
    monotonic (queued → syncing → ready/failed)."""

    __slots__ = ("coordinate", "status", "interpreter", "error", "tail",
                 "started_at", "path", "commit")

    def __init__(self, coordinate: str):
        self.coordinate = coordinate
        self.status = QUEUED
        self.interpreter: "str | None" = None
        self.error: "str | None" = None
        self.tail: "str | None" = None
        self.started_at = time.monotonic()
        self.path: "str | None" = None      # staged checkout (managed jobs)
        self.commit: "str | None" = None     # resolved source_version (managed jobs)

    def snapshot(self) -> dict:
        """A poll-friendly view. ``phase`` mirrors ``status`` (coarse, per §4)."""
        d = {
            "coordinate": self.coordinate,
            "status": self.status,
            "phase": self.status,
            "elapsed_s": round(time.monotonic() - self.started_at, 3),
        }
        if self.path is not None:
            d["path"] = self.path
            d["commit"] = self.commit
        if self.status == READY:
            d["interpreter"] = self.interpreter
        if self.status == FAILED:
            d["error"] = self.error
            d["tail"] = self.tail or ""
        return d


class MaterializationRegistry:
    """Coordinate-keyed registry of async materialization jobs (deduped)."""

    def __init__(self) -> None:
        self._jobs: dict[str, MaterializationJob] = {}
        self._lock = threading.Lock()

    def start(self, source: Path, *, timeout: "float | None" = None) -> MaterializationJob:
        """Begin (or attach to) materialization of ``source``'s environment.

        - Already materialized (cached venv) → a ``ready`` job immediately.
        - In flight (``queued``/``syncing``) → the **same** job (dedup, §5).
        - Otherwise → a new job; a background thread runs ``uv sync`` and drives it
          to ``ready`` or ``failed``. A previously ``failed`` coordinate is retried
          here (an explicit start = the user's retry, §4)."""
        coordinate = environment_coordinate(source)
        with self._lock:
            existing = self._jobs.get(coordinate)
            if existing is not None and existing.status in _ACTIVE:
                return existing
            if existing is not None and existing.status == READY:
                return existing
            job = MaterializationJob(coordinate)
            self._jobs[coordinate] = job

        # Fast path: a venv for this coordinate already exists (§5) — no uv sync.
        cached = cached_interpreter(coordinate)
        if cached is not None:
            job.interpreter = cached
            job.status = READY
            return job

        t = threading.Thread(
            target=self._run, args=(job, Path(source), timeout),
            name=f"materialize-{coordinate}", daemon=True)
        t.start()
        return job

    def start_managed(self, repo: str, ref: str, *,
                      timeout: "float | None" = None) -> MaterializationJob:
        """Begin (or attach to) materialization of a managed ``(repo, ref)``: an
        async **clone → sync** job (``cloning → syncing → ready|failed``). Keyed by
        ``(repo, ref)`` and deduped like `start`. The venv inside is still
        coordinate-keyed by the staged lock, so two `(repo, ref)` resolving to the
        same lock share one venv."""
        key = "repo:" + _managed_key(repo, ref)
        with self._lock:
            existing = self._jobs.get(key)
            if existing is not None and existing.status in (*_ACTIVE, READY):
                return existing
            job = MaterializationJob(key)
            self._jobs[key] = job
        t = threading.Thread(
            target=self._run_managed, args=(job, repo, ref, timeout),
            name=f"materialize-{key}", daemon=True)
        t.start()
        return job

    def status(self, coordinate: str) -> "dict | None":
        job = self._jobs.get(coordinate)
        return job.snapshot() if job is not None else None

    def status_for(self, source: Path) -> "dict | None":
        return self.status(environment_coordinate(source))

    def size(self) -> int:
        with self._lock:
            return len(self._jobs)

    # -- internals ----------------------------------------------------------
    def _run(self, job: MaterializationJob, source: Path, timeout: "float | None") -> None:
        job.status = SYNCING
        try:
            interp = materialize(source) if timeout is None else materialize(source, timeout=timeout)
            job.interpreter = interp
            job.status = READY
        except MaterializationError as e:
            job.error = str(e)
            job.tail = e.tail
            job.status = FAILED
        except Exception as e:  # noqa: BLE001 — any failure is a handled terminal state (§6)
            job.error = str(e)
            job.status = FAILED

    def _run_managed(self, job: MaterializationJob, repo: str, ref: str,
                     timeout: "float | None") -> None:
        from vivarium_workbench.lib import repo_source
        try:
            job.status = CLONING
            staged = (repo_source.stage(repo, ref) if timeout is None
                      else repo_source.stage(repo, ref, timeout=timeout))
            job.path = str(staged.path)
            job.commit = staged.commit
        except repo_source.RepoStagingError as e:
            job.error = str(e)
            job.tail = e.tail
            job.status = FAILED
            return
        except Exception as e:  # noqa: BLE001
            job.error = str(e)
            job.status = FAILED
            return
        try:
            job.status = SYNCING
            interp = (materialize(staged.path) if timeout is None
                      else materialize(staged.path, timeout=timeout))
            job.interpreter = interp
            job.status = READY
        except MaterializationError as e:
            job.error = str(e)
            job.tail = e.tail
            job.status = FAILED
        except Exception as e:  # noqa: BLE001
            job.error = str(e)
            job.status = FAILED


# ---------------------------------------------------------------------------
# Process-wide singleton (a later slice binds jobs per-session via bind/switch).
# ---------------------------------------------------------------------------
_registry: "MaterializationRegistry | None" = None
_registry_lock = threading.Lock()


def get_registry() -> MaterializationRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = MaterializationRegistry()
    return _registry

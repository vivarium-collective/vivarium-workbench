"""Background-thread job manager for investigation-wide multi-variant runs.

The ``/api/investigation-run-unblocked`` endpoint kicks off a sequence
of variant runs (potentially tens of minutes total). HTTP requests can
not block that long, so the work is queued onto a background thread
and tracked here. Clients poll
``/api/investigation-run-unblocked-status?job_id=...`` for progress.

A "job" is one investigation-wide run sequence. It holds:

  job_id       opaque short id (caller polls with this)
  status       queued | running | done | failed
  investigation slug
  items        list of variant-level sub-jobs, each:
                 {study, variant, status, run_id?, error?}
  started_at   ISO8601
  completed_at ISO8601 (set when status reaches done/failed)
  worker       Thread instance (not serialised)

Jobs live in-process; restarting the dashboard loses them. The runs
themselves write to ``studies/<slug>/runs.db`` and that persistence is
the durable artefact — the in-memory job is just progress signalling.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunJob:
    def __init__(self, investigation: str, items: list[dict]):
        self.job_id = uuid.uuid4().hex[:12]
        self.investigation = investigation
        self.items: list[dict] = items  # mutated as work progresses
        self.status = "queued"
        self.started_at = _now()
        self.completed_at: str | None = None
        self.error: str | None = None
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "job_id":        self.job_id,
                "investigation": self.investigation,
                "status":        self.status,
                "items":         [dict(it) for it in self.items],
                "started_at":    self.started_at,
                "completed_at":  self.completed_at,
                "error":         self.error,
                "progress":      self._progress_locked(),
            }

    def _progress_locked(self) -> dict:
        n = len(self.items)
        done = sum(1 for it in self.items if it.get("status") in ("done", "failed", "skipped"))
        running = sum(1 for it in self.items if it.get("status") == "running")
        return {"total": n, "done": done, "running": running}

    def update_item(self, idx: int, **fields) -> None:
        with self._lock:
            if 0 <= idx < len(self.items):
                self.items[idx].update(fields)


class RunJobManager:
    """In-process registry of background run-jobs."""

    def __init__(self):
        self._jobs: dict[str, RunJob] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        investigation: str,
        items: list[dict],
        worker_fn: Callable[[RunJob], None],
    ) -> RunJob:
        """Create a RunJob, start its background worker, return the handle."""
        job = RunJob(investigation, items)
        with self._lock:
            self._jobs[job.job_id] = job

        def _run():
            try:
                with job._lock:
                    job.status = "running"
                worker_fn(job)
                with job._lock:
                    if all(it.get("status") in ("done", "failed", "skipped") for it in job.items):
                        any_failed = any(it.get("status") == "failed" for it in job.items)
                        job.status = "failed" if any_failed else "done"
                    else:
                        job.status = "done"
            except BaseException as e:  # noqa: BLE001
                with job._lock:
                    job.status = "failed"
                    job.error = f"worker crashed: {e}\n{traceback.format_exc()[-2000:]}"
            finally:
                with job._lock:
                    job.completed_at = _now()

        t = threading.Thread(target=_run, daemon=True, name=f"runjob-{job.job_id}")
        job._worker = t
        t.start()
        return job

    def get(self, job_id: str) -> RunJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        # Most recent first
        jobs.sort(key=lambda j: j.started_at, reverse=True)
        return [j.to_dict() for j in jobs[:limit]]


# Module-level singleton — one manager per dashboard process.
manager = RunJobManager()


# ---------------------------------------------------------------------------
# Investigation-level "run unblocked" planner
# ---------------------------------------------------------------------------

def enumerate_unblocked(spec: dict) -> tuple[list[dict], list[dict]]:
    """Return (runnable_items, blocked_items) for one study's spec.

    A variant is **blocked** if the study has any ``conditions.model_settings``
    (or legacy ``expert_inputs``) entry with ``gate: required-before-run``
    whose ``current`` is null / missing. The variant is **runnable**
    otherwise.

    Items have shape ``{study, variant, base_composite?, params?, kind}``
    where kind ∈ {"baseline", "variant"}.

    A study with no variants surfaces its baseline as a single runnable item.
    """
    cond = spec.get("conditions") or {}
    # Backward-compat: accept either model_settings or the legacy alias.
    settings = cond.get("model_settings") or cond.get("expert_inputs") or []
    pending_required = [
        s for s in settings
        if isinstance(s, dict)
        and s.get("gate") == "required-before-run"
        and (s.get("current") is None or s.get("current") == "")
    ]
    blocked_reason = None
    if pending_required:
        names = ", ".join(s.get("name", "?") for s in pending_required)
        blocked_reason = f"required-before-run settings unset: {names}"

    study_slug = spec.get("name") or "(unnamed)"
    runnable: list[dict] = []
    blocked: list[dict] = []

    # Baseline as the implicit first item.
    baseline = cond.get("baseline") or {}
    if baseline.get("composite"):
        item = {
            "study":          study_slug,
            "variant":        "baseline",
            "kind":           "baseline",
            "composite":      baseline.get("composite"),
            "params":         dict(baseline.get("params") or {}),
            "status":         "queued",
        }
        if blocked_reason:
            item["status"] = "blocked"
            item["error"] = blocked_reason
            blocked.append(item)
        else:
            runnable.append(item)

    for v in cond.get("variants") or []:
        if not isinstance(v, dict):
            continue
        item = {
            "study":          study_slug,
            "variant":        v.get("name", "?"),
            "kind":           "variant",
            "composite":      v.get("composite") or v.get("base_composite") or baseline.get("composite"),
            "params":         dict(v.get("parameter_overrides") or v.get("params") or {}),
            "status":         "queued",
        }
        if blocked_reason:
            item["status"] = "blocked"
            item["error"] = blocked_reason
            blocked.append(item)
        else:
            runnable.append(item)

    return runnable, blocked

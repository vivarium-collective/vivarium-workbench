"""Background orchestration for remote (smsvpctest) simulation runs.

Mirrors lib/run_jobs.py but models ONE multi-step pipeline per job (push →
build → run → poll → download → land) rather than many items. The pipeline
worker is injected (see run_remote_pipeline) so it can be unit-tested with
fakes — this module has no network/git/sms-api knowledge itself.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable

STEP_NAMES = ["push", "build", "run", "poll", "download", "land"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class RemoteRunJob:
    def __init__(self, study: str) -> None:
        self.job_id = uuid.uuid4().hex[:12]
        self.study = study
        self.status = "queued"
        self.steps = [{"name": n, "status": "pending", "message": ""} for n in STEP_NAMES]
        self.run_id: str | None = None
        self.error: str | None = None
        self.started_at = _now()
        self.completed_at: str | None = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def set_step(self, name: str, status: str, message: str = "") -> None:
        with self._lock:
            for s in self.steps:
                if s["name"] == name:
                    s["status"] = status
                    if message:
                        s["message"] = message
                    break

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "job_id": self.job_id,
                "study": self.study,
                "status": self.status,
                "steps": [dict(s) for s in self.steps],
                "run_id": self.run_id,
                "error": self.error,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
            }


class RemoteRunManager:
    def __init__(self) -> None:
        self._jobs: dict[str, RemoteRunJob] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def submit(self, study: str, worker_fn: Callable[[RemoteRunJob], None]) -> RemoteRunJob:
        job = RemoteRunJob(study)
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)

        def _run() -> None:
            job.status = "running"
            try:
                worker_fn(job)
                job.status = "failed" if job.error else "done"
            except Exception:  # noqa: BLE001 — record any worker failure on the job
                job.error = traceback.format_exc(limit=4)
                job.status = "failed"
            finally:
                job.completed_at = _now()

        t = threading.Thread(target=_run, daemon=True)
        job._worker = t
        t.start()
        return job

    def get(self, job_id: str) -> RemoteRunJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, n: int = 10) -> list[dict]:
        with self._lock:
            ids = self._order[-n:][::-1]
            jobs = [self._jobs[i] for i in ids]
        return [j.to_dict() for j in jobs]


manager = RemoteRunManager()

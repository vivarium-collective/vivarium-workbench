"""Pure read-only helper for the in-memory job-status GET routes.

Two routes share byte-identical logic, parameterised only by which in-process
job manager they read:

  GET /api/investigation-run-unblocked-status  → ``lib.run_jobs.manager``
  GET /api/remote-run-status                   → ``lib.remote_run_jobs.manager``

``job_status`` takes the manager as a *parameter* so it stays pure and trivially
testable with a fake manager — it never imports the manager singletons itself,
so it never couples to process-global state.  No ``import server`` here.

The manager need only expose:

  * ``list_recent(n: int) -> list[dict]``
  * ``get(job_id: str) -> <job-with-.to_dict()> | None``

The returned job's ``.to_dict()`` shape is manager-specific (``RunJob`` emits
``items[]``, ``RemoteRunJob`` emits ``steps[]``), so callers pass the 200 body
through a pass-through pydantic model rather than a declared-field one.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class _Job(Protocol):
    def to_dict(self) -> dict: ...


class _JobManager(Protocol):
    def list_recent(self, n: int = ..., /) -> list[dict]: ...
    def get(self, job_id: str, /) -> Optional[_Job]: ...


def job_status(manager: _JobManager, job_id: Optional[str]) -> tuple[dict, int]:
    """Resolve a job-status response body + HTTP status for one manager.

    Behaviour-preserving port of the two ``_get_*`` handlers' shared body:

      * empty ``job_id``  → ``({"jobs": manager.list_recent(10)}, 200)``
      * unknown ``job_id``→ ``({"error": "job not found"}, 404)``
      * known ``job_id``  → ``(job.to_dict(), 200)``
    """
    if not job_id:
        return {"jobs": manager.list_recent(10)}, 200
    job: Any = manager.get(job_id)
    if job is None:
        return {"error": "job not found"}, 404
    return job.to_dict(), 200

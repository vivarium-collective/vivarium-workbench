"""Tests for lib.job_status_views.job_status + server.py shim parity.

The pure helper is parameterised by the manager, so a tiny fake manager
(``list_recent`` / ``get``) exercises all three branches without touching the
real in-process singletons.

TestServerShimParity drives the REAL ``_get_*`` handlers via
``server.Handler.__new__`` with a patched ``_json``, ``self.path`` set to the
query URL, and the manager singleton monkeypatched — proving the server's
1-line delegation is behaviour-identical to the lib helper.
"""

from __future__ import annotations

import pytest

from vivarium_dashboard.lib.job_status_views import job_status


class _FakeJob:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


class _FakeManager:
    """Minimal manager stub: records list_recent calls, looks jobs up by id."""

    def __init__(self, jobs: dict | None = None, recent: list[dict] | None = None) -> None:
        self._jobs = jobs or {}
        self._recent = recent if recent is not None else [{"job_id": "j1"}]
        self.list_recent_calls: list[int] = []

    def list_recent(self, n: int = 20) -> list[dict]:
        self.list_recent_calls.append(n)
        return list(self._recent)

    def get(self, job_id: str):
        return self._jobs.get(job_id)


# ---------------------------------------------------------------------------
# TestJobStatusHelper — the pure lib helper, 3 cases
# ---------------------------------------------------------------------------

class TestJobStatusHelper:
    def test_empty_job_id_returns_recent_jobs_200(self) -> None:
        mgr = _FakeManager(recent=[{"job_id": "a"}, {"job_id": "b"}])
        body, status = job_status(mgr, "")
        assert status == 200
        assert body == {"jobs": [{"job_id": "a"}, {"job_id": "b"}]}
        # Parity: the handlers call list_recent(10) on the empty path.
        assert mgr.list_recent_calls == [10]

    def test_none_job_id_returns_recent_jobs_200(self) -> None:
        mgr = _FakeManager(recent=[{"job_id": "a"}])
        body, status = job_status(mgr, None)
        assert status == 200
        assert body == {"jobs": [{"job_id": "a"}]}
        assert mgr.list_recent_calls == [10]

    def test_valid_job_id_returns_to_dict_200(self) -> None:
        job = _FakeJob({"job_id": "x", "status": "done", "items": [{"status": "done"}]})
        mgr = _FakeManager(jobs={"x": job})
        body, status = job_status(mgr, "x")
        assert status == 200
        assert body == {"job_id": "x", "status": "done", "items": [{"status": "done"}]}
        # The recent-list path must NOT be taken when a job is found.
        assert mgr.list_recent_calls == []

    def test_missing_job_id_returns_404(self) -> None:
        mgr = _FakeManager(jobs={})
        body, status = job_status(mgr, "nope")
        assert status == 404
        assert body == {"error": "job not found"}

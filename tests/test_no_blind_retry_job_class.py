"""A job-class method that loses its worker is reported, never repeated.

THE BUG this pins, which was live: `ENV_WORKER_CALL_TIMEOUT` (60 s default) is a
SOCKET timeout, not a deadline with cancellation — nothing tells the worker to
stop. `env_worker._run_study` runs a study's baseline and every declared variant
to completion through blocking subprocesses, so it exceeds 60 s as a matter of
course. `WorkerPool.call` then dropped the worker and re-ran the whole study,
while the first run carried on writing to the same runs.db. Two concurrent
simulations, one orphaned and unattributable.

Retry remains correct for interactive calls — a query that did not happen costs
nothing to repeat — so these tests pin the DIFFERENCE, not a blanket rule.
"""
from __future__ import annotations

import pytest

from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
from vivarium_workbench.lib.env_worker_pool import WorkerPool
from vivarium_workbench.lib.env_worker_routing import JOB_CLASS_METHODS


class _Worker:
    """Fails the first call, succeeds after — so a retry is VISIBLE as success."""

    def __init__(self, log: list[str], fail_first: bool = True):
        self._log = log
        self._fail_first = fail_first

    def call(self, method, params=None, *, timeout=None):
        self._log.append(method)
        if self._fail_first and len(self._log) == 1:
            raise EnvWorkerUnavailable("socket timed out")
        return {"ok": True, "attempt": len(self._log)}

    def alive(self):
        return True

    def close(self):
        pass


class _Launcher:
    kind = "remote"

    def __init__(self, log: list[str]):
        self._log = log

    def env_key(self, workspace):
        return "image:abc123"

    def launch(self, workspace, *, interpreter, timeout):
        return _Worker(self._log)


def _pool(log):
    return WorkerPool(launcher=_Launcher(log))


@pytest.mark.parametrize("method", sorted(JOB_CLASS_METHODS))
def test_a_job_class_method_is_never_run_twice(tmp_path, method):
    """The load-bearing assertion: ONE attempt. Before the fix this was two, and
    the second was a whole second simulation writing to the same runs.db."""
    log: list[str] = []
    with pytest.raises(EnvWorkerUnavailable) as ei:
        _pool(log).call(tmp_path, method)
    assert log == [method], f"{method} was attempted {len(log)} times: {log}"
    msg = str(ei.value)
    assert "NOT retried" in msg
    assert "may still be running" in msg, "the caller must learn the work may be live"
    assert "runs.db" in msg, "and where to look before resubmitting"


def test_an_interactive_method_still_retries_once(tmp_path):
    """Protocol §9 is still right for a query: it did not happen, and repeating
    it is free. Removing the retry wholesale would have been the wrong fix."""
    log: list[str] = []
    result = _pool(log).call(tmp_path, "registry_catalog")
    assert result["ok"] is True
    assert log == ["registry_catalog", "registry_catalog"], log


def test_run_process_retries_because_it_is_not_job_class(tmp_path):
    """`run_process` is a Composite-Explorer probe — one class, one update() —
    and was once misclassified on the `run_` prefix. It must keep retrying, or
    this fix silently degrades the explorer."""
    log: list[str] = []
    assert _pool(log).call(tmp_path, "run_process")["ok"] is True
    assert len(log) == 2, log


def test_a_healthy_job_class_call_is_unaffected(tmp_path):
    """The guard fires only on a lost connection; the normal path is untouched."""
    log: list[str] = []
    pool = WorkerPool(launcher=type("L", (_Launcher,), {})(log))
    pool._launcher.launch = lambda ws, *, interpreter, timeout: _Worker(log, fail_first=False)  # type: ignore[attr-defined]
    assert pool.call(tmp_path, "run_study")["ok"] is True
    assert log == ["run_study"], log

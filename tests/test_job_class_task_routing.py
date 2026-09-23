"""Job-class methods run as durable TASKS where the transport can record them.

Plan §E option (e) step 5. Step 1 stopped the pool silently re-running a study
that lost its worker; this stops the loss happening at all, on the one transport
that can record the work: the proxy, where viva-api owns the socket and can keep
going after the client stops waiting.

The distinction these pin is WHICH TRANSPORT, not which method. A local
subprocess and a dial-back worker are both held by this process, so there is
nowhere durable for a task record to live — routing to a task tier that does not
exist would be worse than not routing.
"""
from __future__ import annotations

import pytest

from vivarium_workbench.lib.env_worker_client import EnvWorkerError, EnvWorkerUnavailable
from vivarium_workbench.lib.env_worker_launcher import ProxyEnvWorker
from vivarium_workbench.lib.env_worker_pool import WorkerPool


class _TaskWorker:
    """A proxy-shaped worker: it can run a call as a task."""

    supports_tasks = True

    def __init__(self, log: list[str]):
        self._log = log

    def call(self, method, params=None, *, timeout=None):
        self._log.append(f"sync:{method}")
        return {"via": "sync"}

    def call_task(self, method, params=None):
        self._log.append(f"task:{method}")
        return {"via": "task"}

    def alive(self):
        return True

    def close(self):
        pass


class _SocketWorker(_TaskWorker):
    """A local or dial-back worker: no task tier anywhere to record into."""

    supports_tasks = False

    def call_task(self, method, params=None):  # pragma: no cover - must never run
        raise AssertionError("a transport without a task tier must not be asked for one")


class _Launcher:
    def __init__(self, worker, kind="proxy"):
        self._worker, self.kind = worker, kind

    def env_key(self, workspace):
        return "image:abc"

    def launch(self, workspace, *, interpreter, timeout):
        return self._worker


def _pool(worker, kind="proxy"):
    return WorkerPool(launcher=_Launcher(worker, kind))


@pytest.mark.parametrize("method", ["run_study", "run_study_analyses", "run_investigation_analysis"])
def test_job_class_goes_to_the_task_tier_on_the_proxy(tmp_path, method):
    log: list[str] = []
    assert _pool(_TaskWorker(log)).call(tmp_path, method) == {"via": "task"}
    assert log == [f"task:{method}"]


def test_interactive_methods_stay_synchronous(tmp_path):
    """The task tier is for work that cannot be a request. An interactive call
    answers in milliseconds; routing it through submit+poll would add a poll
    interval of latency to every registry query in the UI."""
    log: list[str] = []
    assert _pool(_TaskWorker(log)).call(tmp_path, "registry_catalog") == {"via": "sync"}
    assert log == ["sync:registry_catalog"]


def test_run_process_stays_synchronous(tmp_path):
    """Not job-class — a Composite-Explorer probe, one class and one update().
    It was once misclassified on the `run_` prefix; this is the guard."""
    log: list[str] = []
    assert _pool(_TaskWorker(log)).call(tmp_path, "run_process") == {"via": "sync"}
    assert log == ["sync:run_process"]


@pytest.mark.parametrize("method", ["run_study", "registry_catalog"])
def test_a_transport_without_a_task_tier_is_never_asked_for_one(tmp_path, method):
    """THE routing rule. Local and dial-back workers are held by this process,
    so a task record would have nowhere to survive; they keep today's behaviour
    and step 1's refusal remains their protection."""
    log: list[str] = []
    assert _pool(_SocketWorker(log)).call(tmp_path, method) == {"via": "sync"}
    assert log == [f"sync:{method}"]


# --- ProxyEnvWorker.call_task ----------------------------------------------


class _Client:
    def __init__(self, statuses, submit=None, raise_on_poll=None):
        self._statuses = list(statuses)
        self._submit = submit if submit is not None else {"task_id": 7}
        self._raise_on_poll = raise_on_poll
        self.submitted: list[dict] = []
        self.polls = 0

    def submit_env_worker_task(self, job_name, *, method, params=None):
        self.submitted.append({"job": job_name, "method": method, "params": params})
        return self._submit

    def get_env_worker_task(self, task_id):
        self.polls += 1
        if self._raise_on_poll:
            raise self._raise_on_poll
        return self._statuses.pop(0)

    def stop_relayed_env_worker(self, job_name):
        return {}


def _worker(client) -> ProxyEnvWorker:
    w = ProxyEnvWorker(client, "job-1", "/ws", timeout=30)
    w.TASK_POLL_INTERVAL = 0  # type: ignore[misc]
    return w


def test_call_task_polls_to_completion_and_returns_the_result():
    client = _Client([{"status": "queued"}, {"status": "running"},
                      {"status": "completed", "result": {"run_refs": [1, 2]}}])
    assert _worker(client).call_task("run_study", {"study": "s1"}) == {"run_refs": [1, 2]}
    assert client.submitted[0]["method"] == "run_study"
    assert client.polls == 3


def test_a_failed_task_raises_with_the_workers_own_message():
    client = _Client([{"status": "failed", "error_message": "study spec is invalid"}])
    with pytest.raises(EnvWorkerError, match="study spec is invalid"):
        _worker(client).call_task("run_study")


def test_a_cancelled_task_is_an_error_not_a_silent_none():
    client = _Client([{"status": "cancelled", "error_message": "cancelled by kr0@stanford.edu"}])
    with pytest.raises(EnvWorkerError, match="cancelled by"):
        _worker(client).call_task("run_study")


def test_a_submit_with_no_task_id_fails_loudly():
    with pytest.raises(EnvWorkerUnavailable, match="no task_id"):
        _worker(_Client([], submit={})).call_task("run_study")


def test_losing_the_poll_says_the_work_may_still_be_running():
    """A lost poll is not a lost task: the record is durable, so the error must
    point at the task rather than imply the work died."""
    client = _Client([], raise_on_poll=RuntimeError("connection reset"))
    with pytest.raises(EnvWorkerUnavailable) as ei:
        _worker(client).call_task("run_study")
    msg = str(ei.value)
    assert "may still be running" in msg
    assert "task 7" in msg


def test_a_timeout_does_not_cancel_and_says_so():
    """Giving up watching must not be mistaken for stopping the work — that
    confusion is how the double-run started."""
    client = _Client([{"status": "running"}] * 50)
    w = _worker(client)
    w.TASK_TIMEOUT = 0  # type: ignore[misc]
    with pytest.raises(EnvWorkerUnavailable) as ei:
        w.call_task("run_study")
    assert "NOT cancelled" in str(ei.value)


def test_a_closed_worker_does_not_submit():
    client = _Client([])
    w = _worker(client)
    w.close()
    with pytest.raises(EnvWorkerUnavailable, match="closed"):
        w.call_task("run_study")
    assert client.submitted == []

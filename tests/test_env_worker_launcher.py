"""How an env worker is created — local subprocess vs remote image-as-worker.

The choice is a deployment-wide decision at one composition root (§2A.8 /
REFACTOR-PLAN §5C.4), not a per-call switch, so the tests here are mostly about
*which* launcher a deployment gets and what the remote one does when the pod
never calls home — the failure that would otherwise hang a session.
"""
import json
import socket
import struct
import threading
from pathlib import Path

import pytest

from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
from vivarium_workbench.lib.env_worker_launcher import LocalWorkerLauncher, default_launcher
from vivarium_workbench_sms.env_workers import RemoteWorkerLauncher


class _FakeClient:
    """Stands in for viva-api. Records calls; optionally dials back for real."""

    def __init__(self, *, dial: bool = True, logs: str | None = None):
        self.started: list[dict] = []
        self.stopped: list[str] = []
        self.status_calls: list[str] = []
        self._dial = dial
        self._logs = logs

    def start_env_worker(self, **kw):
        self.started.append(kw)
        if self._dial:
            threading.Thread(target=self._connect, args=(kw["callback_port"], kw["token"]),
                             daemon=True).start()
        return {"job_name": "env-worker-234dc76-abc", "image": "ecr/v2ecoli:234dc76"}

    @staticmethod
    def _connect(port: int, token: str) -> None:
        c = socket.create_connection(("127.0.0.1", port), timeout=10)
        body = json.dumps({"token": token}).encode()
        c.sendall(struct.pack(">I", len(body)) + body)
        c.recv(1)          # hold the socket open so the pool sees a live worker

    def env_worker_status(self, job_name, include_logs=False):
        self.status_calls.append(job_name)
        return {"job_name": job_name, "logs": self._logs}

    def stop_env_worker(self, job_name):
        self.stopped.append(job_name)
        return {"status": "deleted"}


def _stamped(tmp_path: Path, commit: str = "234dc76") -> Path:
    (tmp_path / ".viv-build.json").write_text(json.dumps({"simulator_id": 82, "commit": commit}))
    return tmp_path


# --- the composition root ---------------------------------------------------

def test_deployment_without_an_advertise_host_gets_the_local_launcher(monkeypatch):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST", raising=False)
    assert isinstance(default_launcher(), LocalWorkerLauncher)


def test_deployment_that_declares_a_dial_back_host_gets_the_remote_launcher(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST", "10.99.45.175")
    assert isinstance(default_launcher(), RemoteWorkerLauncher)


def test_remote_launcher_talks_to_the_configured_api_not_localhost(monkeypatch):
    """A bare SmsApiClient() takes its localhost:8080 default, which inside a pod
    is the workbench itself — every launch would fail to reach viva-api. Caught
    on the live dev pod, where SMS_API_BASE is http://api:8000."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST", "10.99.45.175")
    monkeypatch.setenv("SMS_API_BASE", "http://api:8000")
    monkeypatch.delenv("VIVA_API_BASE", raising=False)
    launcher = default_launcher()
    assert launcher._client.base_url == "http://api:8000"


def test_blank_host_is_treated_as_unset_not_as_a_dial_back_target(monkeypatch):
    """An empty env var is how a manifest says "not configured" — it must not
    produce a remote launcher that tells workers to dial back to ''."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST", "   ")
    assert isinstance(default_launcher(), LocalWorkerLauncher)


# --- the remote path --------------------------------------------------------

def test_remote_launcher_asks_for_the_workspaces_own_commit(tmp_path):
    client = _FakeClient()
    launcher = RemoteWorkerLauncher(client, advertise_host="10.0.0.7", bind_host="127.0.0.1")
    worker = launcher.launch(str(_stamped(tmp_path)), interpreter=None, timeout=30)
    try:
        req = client.started[0]
        assert req["commit"] == "234dc76"
        assert req["callback_host"] == "10.0.0.7"
        assert req["callback_port"] > 0 and req["token"]
    finally:
        worker.close()


def test_closing_a_remote_worker_deletes_its_job(tmp_path):
    """Otherwise a pod outlives its only client and is reachable by nobody —
    the leak the TTL backstop exists to catch, not the normal path."""
    client = _FakeClient()
    launcher = RemoteWorkerLauncher(client, advertise_host="10.0.0.7", bind_host="127.0.0.1")
    worker = launcher.launch(str(_stamped(tmp_path)), interpreter=None, timeout=30)
    worker.close()
    assert client.stopped == ["env-worker-234dc76-abc"]


def test_unstamped_workspace_is_refused_with_the_reason(tmp_path):
    """Hosted requires every served workspace to be image-backed (§2A.8)."""
    client = _FakeClient()
    launcher = RemoteWorkerLauncher(client, advertise_host="10.0.0.7", bind_host="127.0.0.1")
    with pytest.raises(EnvWorkerUnavailable, match="viv-build.json"):
        launcher.launch(str(tmp_path), interpreter=None, timeout=30)
    assert client.started == []          # never asked for a Job it could not name


def test_worker_that_never_dials_back_is_cleaned_up_and_reports_its_logs(tmp_path, monkeypatch):
    """A pod that fails to start would otherwise linger until TTL, and the
    timeout alone says nothing about why."""
    monkeypatch.setattr(
        "vivarium_workbench_sms.env_workers.REMOTE_START_TIMEOUT", 0.4)
    client = _FakeClient(dial=False, logs="unrecognized arguments: --connect-to")
    launcher = RemoteWorkerLauncher(client, advertise_host="10.0.0.7", bind_host="127.0.0.1")
    with pytest.raises(EnvWorkerUnavailable, match="did not connect") as exc:
        launcher.launch(str(_stamped(tmp_path)), interpreter=None, timeout=30)
    assert "unrecognized arguments" in str(exc.value)     # the worker's own reason
    assert client.stopped == ["env-worker-234dc76-abc"]   # and no leaked Job

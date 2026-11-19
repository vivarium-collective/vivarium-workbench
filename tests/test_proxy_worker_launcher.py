"""Plan §C/§C1 — the PROXY transport: viva-api holds the socket, we call over HTTP.

Exists because a laptop cannot be dialled: its SSM tunnel is laptop-initiated
with no inbound path, so `RemoteWorkerLauncher`'s dial-back has no address to
advertise. Here the workbench binds no listener and needs no reachable address.

These pin the three-way composition root, the pool-key separation, and the
failure normalisation — the places where a wrong answer is silent rather than
loud.
"""
from __future__ import annotations

import json

import pytest

from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
from vivarium_workbench.lib.env_worker_launcher import LocalWorkerLauncher, default_launcher
from vivarium_workbench_sms.env_workers import (
    ProxyEnvWorker,
    ProxyWorkerLauncher,
    RemoteWorkerLauncher,
)


class _FakeClient:
    def __init__(self, *, start=None, call=None, raises=None):
        self.started: list[dict] = []
        self.calls: list[dict] = []
        self.stopped: list[str] = []
        self._start = start if start is not None else {"job_name": "job-1"}
        self._call = call if call is not None else {"result": {"ok": True}}
        self._raises = raises

    def start_relayed_env_worker(self, **kw):
        self.started.append(kw)
        return self._start

    def call_relayed_env_worker(self, job_name, **kw):
        self.calls.append({"job_name": job_name, **kw})
        if self._raises:
            raise self._raises
        return self._call

    def stop_relayed_env_worker(self, job_name):
        self.stopped.append(job_name)
        return {"status": "deleted"}


def _stamped(tmp_path, commit="abc123"):
    (tmp_path / ".viv-build.json").write_text(json.dumps({"commit": commit}))
    return str(tmp_path)


# --- the composition root ------------------------------------------------- #

def test_proxy_wins_over_a_leftover_advertise_host(monkeypatch):
    """A site switching to the relay may still carry ADVERTISE_HOST from the
    dial-back configuration. Proxy is the only transport that works where the
    workbench cannot be dialled, so setting it must MEAN it — being silently
    shadowed here is a bug that looks like a hung worker."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", "http://api:8000")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST", "10.0.0.9")
    assert isinstance(default_launcher(), ProxyWorkerLauncher)


def test_without_proxy_the_dial_back_launcher_is_unchanged(monkeypatch):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", raising=False)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST", "10.0.0.9")
    assert isinstance(default_launcher(), RemoteWorkerLauncher)


def test_with_neither_it_is_still_a_local_subprocess(monkeypatch):
    """A laptop routing to itself through the cloud would be absurd (§E Q4)."""
    monkeypatch.delenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", raising=False)
    monkeypatch.delenv("VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST", raising=False)
    assert isinstance(default_launcher(), LocalWorkerLauncher)


def test_a_blank_proxy_base_is_treated_as_unset(monkeypatch):
    """An empty env var is how a manifest says 'not configured'."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", "   ")
    monkeypatch.delenv("VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST", raising=False)
    assert isinstance(default_launcher(), LocalWorkerLauncher)


# --- pool keying ----------------------------------------------------------- #

def test_kind_differs_from_remote_so_the_pool_cannot_share_an_entry():
    """Same image, different route. A warm dial-back worker handed to a proxy
    caller is a handle it has no way to use."""
    assert ProxyWorkerLauncher.kind == "proxy"
    assert RemoteWorkerLauncher.kind == "remote"
    assert ProxyWorkerLauncher.kind != RemoteWorkerLauncher.kind


def test_env_key_is_the_image_identical_to_the_remote_launcher(tmp_path):
    """The environment does not change because the bytes take a different route;
    `kind` is where the distinction honestly belongs."""
    ws = _stamped(tmp_path, "deadbeef")
    proxy = ProxyWorkerLauncher(_FakeClient())
    remote = RemoteWorkerLauncher(_FakeClient(), advertise_host="10.0.0.1")
    assert proxy.env_key(ws) == "image:deadbeef" == remote.env_key(ws)


def test_an_unstamped_workspace_is_refused_with_a_reason(tmp_path):
    with pytest.raises(EnvWorkerUnavailable, match="no build stamp"):
        ProxyWorkerLauncher(_FakeClient()).env_key(str(tmp_path))


# --- launch + call --------------------------------------------------------- #

def test_launch_asks_for_the_workspaces_own_commit_and_binds_no_listener(tmp_path):
    ws = _stamped(tmp_path, "c0ffee")
    client = _FakeClient()
    worker = ProxyWorkerLauncher(client).launch(ws, interpreter=None, timeout=30)
    assert client.started[0]["commit"] == "c0ffee"
    # No callback host/port/token: we have no address a worker could dial, which
    # is the entire reason this transport exists.
    assert "callback_host" not in client.started[0]
    assert "callback_port" not in client.started[0]
    assert "token" not in client.started[0]
    assert isinstance(worker, ProxyEnvWorker)
    assert worker.alive()


def test_a_start_with_no_job_name_is_an_error_not_a_silent_dud(tmp_path):
    """Otherwise every later call 404s against the empty string."""
    ws = _stamped(tmp_path)
    with pytest.raises(EnvWorkerUnavailable, match="no job_name"):
        ProxyWorkerLauncher(_FakeClient(start={})).launch(ws, interpreter=None, timeout=30)


def test_call_forwards_the_method_and_unwraps_result(tmp_path):
    ws = _stamped(tmp_path)
    client = _FakeClient(call={"result": [1, 2, 3]})
    worker = ProxyWorkerLauncher(client).launch(ws, interpreter=None, timeout=30)
    assert worker.call("list_generators", {"x": 1}) == [1, 2, 3]
    assert client.calls[0]["method"] == "list_generators"
    assert client.calls[0]["params"] == {"x": 1}
    assert client.calls[0]["job_name"] == "job-1"


def test_a_failed_call_marks_the_worker_dead_so_the_pool_stops_handing_it_out(tmp_path):
    """A relayed worker that has gone away must look to the pool exactly like a
    dead local one — otherwise it is served warm, forever, to every caller."""
    ws = _stamped(tmp_path)
    client = _FakeClient(raises=RuntimeError("410 Gone"))
    worker = ProxyWorkerLauncher(client).launch(ws, interpreter=None, timeout=30)
    assert worker.alive()
    with pytest.raises(EnvWorkerUnavailable, match="410 Gone"):
        worker.call("anything")
    assert not worker.alive()


def test_calling_a_closed_worker_does_not_reach_the_network(tmp_path):
    ws = _stamped(tmp_path)
    client = _FakeClient()
    worker = ProxyWorkerLauncher(client).launch(ws, interpreter=None, timeout=30)
    worker.close()
    with pytest.raises(EnvWorkerUnavailable, match="is closed"):
        worker.call("x")
    assert client.calls == []


def test_close_deletes_the_job_and_is_idempotent(tmp_path):
    ws = _stamped(tmp_path)
    client = _FakeClient()
    worker = ProxyWorkerLauncher(client).launch(ws, interpreter=None, timeout=30)
    worker.close()
    worker.close()
    assert client.stopped == ["job-1", "job-1"]
    assert not worker.alive()


def test_close_never_raises_into_the_pool(tmp_path):
    """Teardown runs on eviction paths; an exception there takes the pool with it."""
    ws = _stamped(tmp_path)

    class _Boom(_FakeClient):
        def stop_relayed_env_worker(self, job_name):
            raise RuntimeError("api down")

    worker = ProxyWorkerLauncher(_Boom()).launch(ws, interpreter=None, timeout=30)
    worker.close()          # must not raise
    assert not worker.alive()

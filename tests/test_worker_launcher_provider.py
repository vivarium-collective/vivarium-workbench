"""Hosted env-worker launchers come from the backend provider (docs/backend-adapters.md §2.2, step P2 "Workers").

With providers ON nothing changes: the same launcher for each env combination, the same client base URL, the same
requests to viva-api (pinned here end to end, below the client). With providers OFF (or the plugin absent) a
deployment that asks for hosted workers is REFUSED -- at startup and at launch -- and never quietly gets a local
subprocess. Inside an env worker, generator discovery runs in-process and never asks the pool for a nested worker.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from vivarium_workbench.lib import backends
from vivarium_workbench.lib.backend_config import BackendConfigError
from vivarium_workbench.lib.backends import provider as provider_mod
from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
from vivarium_workbench.lib.env_worker_launcher import LocalWorkerLauncher, default_launcher

_HOSTED = ("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", "VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST",
           "VIVARIUM_DASHBOARD_ENV_WORKER_PROXY_BASE", "VIVARIUM_DASHBOARD_ENV_WORKER_ADVERTISE_HOST")
_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _HOSTED:
        monkeypatch.delenv(name, raising=False)
    backends.reset_registry()
    yield
    backends.reset_registry()


@pytest.fixture
def providers_off(monkeypatch):
    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    yield
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()


@pytest.fixture
def plugin_absent(monkeypatch):
    """The SMS plugin is not installed: its provider module cannot be imported (as test_backend_providers does)."""
    import builtins

    real_import = builtins.__import__

    def absent(name, *a, **k):
        if name == "vivarium_workbench_sms" and a and a[2] and "provider" in a[2]:
            raise ModuleNotFoundError("No module named 'vivarium_workbench_sms.provider'",
                                      name="vivarium_workbench_sms.provider")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", absent)
    provider_mod.reset()
    yield
    monkeypatch.setattr(builtins, "__import__", real_import)
    provider_mod.reset()


# --- providers on: unchanged ------------------------------------------------------------------------------------------

def test_a_truthy_proxy_base_means_the_configured_api(monkeypatch):
    """``PROXY_BASE=true`` (or 1/yes) relays through the deployment's configured viva-api, not a literal 'true'."""
    from vivarium_workbench_sms.env_workers import ProxyWorkerLauncher

    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", "true")
    monkeypatch.setenv("SMS_API_BASE", "http://api:8000")
    monkeypatch.delenv("VIVA_API_BASE", raising=False)
    monkeypatch.delenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", raising=False)
    launcher = default_launcher()
    assert isinstance(launcher, ProxyWorkerLauncher)
    assert launcher.kind == "proxy"
    assert launcher._client.base_url == "http://api:8000"


def test_the_sms_provider_supplies_none_without_hosted_env_and_makes_no_network_call(monkeypatch):
    from vivarium_workbench_sms import sms_api_client

    def no_network(*a, **k):
        raise AssertionError("worker_launcher() must not touch the network")

    monkeypatch.setattr(sms_api_client, "urlopen", no_network)
    (sms,) = provider_mod.providers()
    assert sms.worker_launcher() is None
    assert isinstance(default_launcher(), LocalWorkerLauncher)


# --- providers off / plugin absent: refused, never Local ---------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE",
    "VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST",
    "VIVARIUM_DASHBOARD_ENV_WORKER_PROXY_BASE",   # the deprecated prefix still asks for hosted workers
])
def test_hosted_workers_with_providers_off_are_refused(providers_off, monkeypatch, name):
    monkeypatch.setenv(name, "http://api:8000")
    with pytest.raises(BackendConfigError, match=name) as exc:
        default_launcher()
    assert "BACKEND_PROVIDERS=none" in str(exc.value)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_providers_off_without_hosted_env_is_local(providers_off, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", value)
        monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_ADVERTISE_HOST", value)
    assert isinstance(default_launcher(), LocalWorkerLauncher)


def test_hosted_workers_without_the_plugin_are_refused(plugin_absent, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", "http://api:8000")
    assert provider_mod.providers() == ()
    with pytest.raises(BackendConfigError, match="ENV_WORKER_PROXY_BASE"):
        default_launcher()


def test_the_pool_refuses_on_every_call_and_never_caches_or_degrades(providers_off, monkeypatch, tmp_path, caplog):
    """Not an EnvWorkerUnavailable (soft-degrading callers swallow that), not cached, logged at ERROR once."""
    from vivarium_workbench.lib.env_worker_pool import WorkerPool

    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", "http://api:8000")
    pool = WorkerPool()
    with caplog.at_level("ERROR"):
        for _ in range(2):
            with pytest.raises(BackendConfigError) as exc:
                pool.call(tmp_path, "ping")
            assert not isinstance(exc.value, EnvWorkerUnavailable)
            assert pool._deployment_launcher is None
    assert pool.size() == 0
    assert len([r for r in caplog.records if "no launcher" in r.getMessage()]) == 1


# --- startup refuses ---------------------------------------------------------------------------------------------------

@pytest.fixture
def hosted_but_off(providers_off, monkeypatch, tmp_path):
    for n in ("VIVA_API_BASE", "SMS_API_BASE", "VIVARIUM_WORKBENCH_BACKEND_BASE_URL"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", "http://api:8000")
    (tmp_path / "workspace.yaml").write_text("name: t\n")
    return tmp_path


def _must_not_run(*a, **k):
    raise AssertionError("reached past the startup refusal")


def test_serve_fastapi_refuses_before_uvicorn(hosted_but_off, monkeypatch, capsys):
    import uvicorn

    from vivarium_workbench.lib import startup

    monkeypatch.setattr(uvicorn, "run", _must_not_run)
    assert startup.serve_fastapi(hosted_but_off, 0) == 2
    assert "ERROR:" in capsys.readouterr().err


def test_cli_serve_detach_refuses_before_detaching(hosted_but_off, monkeypatch, capsys):
    from vivarium_workbench import cli

    monkeypatch.setattr(cli, "_serve_detached", _must_not_run)
    assert cli.main(["serve", "--workspace", str(hosted_but_off), "--detach"]) == 2
    assert "ENV_WORKER_PROXY_BASE" in capsys.readouterr().err


def test_cli_foreground_serve_refuses_before_rendering_or_writing_server_info(hosted_but_off, monkeypatch, capsys):
    from vivarium_workbench import cli
    from vivarium_workbench.lib import report

    monkeypatch.setattr(report, "render_dashboard", _must_not_run)
    assert cli.main(["serve", "--workspace", str(hosted_but_off)]) == 2
    assert "ENV_WORKER_PROXY_BASE" in capsys.readouterr().err
    assert not (hosted_but_off / ".pbg" / "server" / "server-info").exists()


def test_a_broken_plugin_is_an_error_line_not_a_traceback(monkeypatch, tmp_path, capsys):
    from vivarium_workbench import cli
    from vivarium_workbench.lib import env_worker_launcher

    def broken():
        raise ModuleNotFoundError("No module named 'some_dep_of_the_plugin'", name="some_dep_of_the_plugin")

    monkeypatch.setattr(env_worker_launcher, "check_worker_config", broken)
    monkeypatch.setattr(cli, "cmd_run_composite", _must_not_run)
    assert cli.main(["run-composite", "--request", str(tmp_path / "req.json")]) == 2
    assert "backend plugin failed to load" in capsys.readouterr().err


def test_cli_backend_commands_refuse_before_running(hosted_but_off, monkeypatch, capsys):
    from vivarium_workbench import cli

    monkeypatch.setattr(cli, "cmd_run_composite", _must_not_run)
    assert cli.main(["run-composite", "--request", str(hosted_but_off / "req.json")]) == 2
    assert "ENV_WORKER_PROXY_BASE" in capsys.readouterr().err


def test_api_main_refuses_before_uvicorn(hosted_but_off, monkeypatch):
    import uvicorn

    from vivarium_workbench.api import __main__ as api_main

    monkeypatch.setattr(uvicorn, "run", _must_not_run)
    monkeypatch.setattr(sys, "argv", ["vivarium_workbench.api", "--workspace", str(hosted_but_off)])
    monkeypatch.setenv("VIVARIUM_WORKBENCH_WORKSPACE", str(hosted_but_off))  # main() writes it; restored after
    with pytest.raises(SystemExit, match="ENV_WORKER_PROXY_BASE"):
        api_main.main()


# --- the wire, end to end with providers on (golden) ----------------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _wire(monkeypatch, capabilities):
    """Record every request the launcher's client sends, below the client (so the path rewrite is included)."""
    from vivarium_workbench_sms import sms_api_client
    from vivarium_workbench_sms.env_workers import ProxyEnvWorker

    sent: list[tuple] = []

    def fake_urlopen(req, timeout=None):
        path = req.full_url.split("http://api:8000", 1)[1]
        body = json.loads(req.data.decode()) if req.data else None
        sent.append((req.get_method(), path, body, timeout))
        if req.get_method() == "DELETE":
            out: dict = {"status": "deleted"}
        elif path.endswith("/call"):
            out = {"result": {"ok": True}}
        elif req.get_method() == "POST" and path.endswith("/tasks"):
            out = {"task_id": 7}
        elif req.get_method() == "GET":
            out = {"status": "completed", "result": {"done": 1}}
        else:
            out = {"job_name": "env-worker-abc"}
        return _Resp(json.dumps(out).encode())

    monkeypatch.setattr(sms_api_client, "urlopen", fake_urlopen)
    monkeypatch.setattr(sms_api_client.SmsApiClient, "_server_capabilities", lambda self: frozenset(capabilities))
    monkeypatch.setattr(ProxyEnvWorker, "TASK_POLL_INTERVAL", 0.0)
    return sent


@pytest.mark.parametrize("capabilities, prefix", [
    ((), "/env-worker/v1/"),
    (("viva-v1-workers",), "/viva/v1/workers/"),
])
def test_the_proxy_launcher_sends_exactly_todays_requests(monkeypatch, tmp_path, capabilities, prefix):
    from vivarium_workbench_sms import sms_api_client

    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", "http://api:8000")
    (tmp_path / ".viv-build.json").write_text(json.dumps({"commit": "abc123"}))
    sms_api_client.SmsApiClient("http://api:8000")._link().mark_up()  # a breaker left open by another test
    sent = _wire(monkeypatch, capabilities)

    launcher = default_launcher()
    assert (launcher.kind, launcher.env_key(str(tmp_path))) == ("proxy", "image:abc123")
    worker = launcher.launch(str(tmp_path), interpreter=None, timeout=30)
    assert worker.call("ping", {"x": 1}) == {"ok": True}
    assert worker.call_task("run_study", {"s": 2}) == {"done": 1}
    worker.close()

    relay = "/env-worker/v1/relay/workers" if not capabilities else "/viva/v1/workers/relay"
    tasks = "/env-worker/v1/tasks" if not capabilities else "/viva/v1/workers/tasks"
    call = ("/env-worker/v1/relay/workers/env-worker-abc/call" if not capabilities
            else "/viva/v1/workers/env-worker-abc/call")
    assert sent == [
        ("POST", relay, {"commit": "abc123", "accept_timeout": 300.0}, 300.0 + sms_api_client.RELAY_HTTP_MARGIN),
        ("POST", call, {"method": "ping", "params": {"x": 1}, "timeout": 30}, 30 + sms_api_client.RELAY_HTTP_MARGIN),
        ("POST", tasks, {"job_name": "env-worker-abc", "method": "run_study", "params": {"s": 2}}, 30.0),
        ("GET", f"{tasks}/7", None, 30.0),
        ("DELETE", f"{relay}/env-worker-abc", None, 30.0),
    ]
    assert all(p.startswith(prefix) for _, p, _, _ in sent)


# --- inside an env worker: discovery in-process, never a nested worker ------------------------------------------------

def test_in_a_worker_generator_discovery_never_reaches_the_pool(monkeypatch):
    from vivarium_workbench.lib import composite_lookup as cl
    from vivarium_workbench.lib import env_worker_launcher, env_worker_pool

    gens = {"pkg.composites.g": {"name": "g"}}
    monkeypatch.setattr(cl, "IN_WORKER_DISCOVER", lambda: {"generators": gens})
    monkeypatch.setattr(env_worker_pool, "get_pool", _must_not_run)
    monkeypatch.setattr(env_worker_launcher, "default_launcher", _must_not_run)
    assert cl._discover_generators_via_worker(Path("/ws")) == gens


@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_composites_full_in_a_worker_with_no_launcher_still_lists_generators(monkeypatch):
    """The hosted worker pod has no backend plugin: a nested pool call there cannot get a launcher (here: providers
    off with PROXY_BASE set, which refuses). The worker must discover in-process and still list its generators."""
    pytest.importorskip("viva_superpowers")
    from vivarium_workbench.lib.env_worker_client import EnvWorker

    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ENV_WORKER_PROXY_BASE", "http://127.0.0.1:9")
    with EnvWorker(_FIXTURE.resolve()) as w:
        full = w.call("composites_full")
        gens = w.call("discover_composites")["generators"]
    assert "error" not in full, full.get("error")
    listed = {c["id"] for c in full["composites"] if c.get("kind") == "generator"}
    assert gens and listed == set(gens)

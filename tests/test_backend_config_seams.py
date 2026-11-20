"""The config seams (docs/backend-adapters.md §2.3, step P3 C1): ``lib/backends/config.py`` reads the core's half
(``backend_config``: the named URL, the neutral deployment default) plus each active provider's (``check_config``,
``base_url``, ``configured``, ``deployment_default``).

The matrix (every environment, providers on and off) is ``test_backend_config_goldens.py``. Pinned here: the
provider's four methods, the plugin-absent mode (a fresh interpreter that cannot import the plugin, as an env worker
built without it), a broken plugin at every process entry, and the CLI's hint when no provider added subcommands.
No module-level plugin import: the plugin is reached through the registry, or imported inside the test that needs it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from vivarium_workbench.lib import backend_config, backends
from vivarium_workbench.lib.backends import config as backend_cfg
from vivarium_workbench.lib.backends import provider as provider_mod

NAMED, VIVA, SMS = backend_config.BACKEND_BASE_ENV_VARS
DEPLOY = "VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in (*backend_config.BACKEND_BASE_ENV_VARS, DEPLOY, "VIVARIUM_DASHBOARD_REMOTE_DEPLOYMENT",
              provider_mod.PROVIDERS_ENV):
        monkeypatch.delenv(v, raising=False)
    backends.reset_registry()
    yield
    backends.reset_registry()


@pytest.fixture
def providers_off(monkeypatch):
    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()


@pytest.fixture
def broken_plugin(monkeypatch):
    def boom():
        raise ImportError("No module named 'some_dependency'")

    monkeypatch.setattr(provider_mod, "providers", boom)


# --- the provider's half (needs the plugin) --------------------------------------------------------------------------

def _sms():
    from vivarium_workbench_sms.provider import PROVIDER

    return PROVIDER


@pytest.mark.plugin
def test_the_sms_provider_answers_its_config(monkeypatch):
    p = _sms()
    assert (p.base_url(), p.configured(), p.deployment_default()) == ("http://localhost:8080", False, "smsvpctest")
    p.check_config(None)  # nothing named: the aliases are the one backend
    monkeypatch.setenv(SMS, "http://api:8000")
    assert (p.base_url(), p.configured()) == ("http://api:8000", True)
    p.check_config("http://API:8000/")
    with pytest.raises(backend_config.BackendConfigError, match="two remote backends"):
        p.check_config("http://compose.test")
    monkeypatch.setenv(NAMED, "http://compose.test")
    assert p.base_url() == "http://compose.test"  # the named backend first, as sms_api_base always read it


@pytest.mark.plugin
def test_the_core_names_the_backend_only_through_the_named_url(monkeypatch):
    """``backend_config`` (which may not ask providers) knows only the named URL; the alias and the tunnel are the
    provider's (``backends.config`` adds them)."""
    monkeypatch.setenv(SMS, "http://api:8000")
    assert backend_config.configured_base_url() is None
    assert backend_config.deployment_name() == "default"
    assert backend_cfg.configured_base_url() == "http://api:8000"
    assert backend_cfg.deployment_name() == "smsvpctest"
    assert backend_cfg.stray_backend_env() == []


def test_providers_off_ignore_an_alias(monkeypatch, providers_off):
    monkeypatch.setenv(NAMED, "http://compose.test")
    monkeypatch.setenv(SMS, "http://api:8000")
    backend_cfg.check_config()  # accepted: the alias is not read
    assert backend_cfg.configured_base_url() == "http://compose.test"
    assert backend_cfg.backend_configured() is True
    assert backend_cfg.stray_backend_env() == [SMS]
    monkeypatch.delenv(NAMED)
    assert (backend_cfg.configured_base_url(), backend_cfg.backend_configured()) == (None, False)


def test_remote_health_asks_nothing_without_a_url(monkeypatch, providers_off):
    from vivarium_workbench.lib import workspace_deps_views

    monkeypatch.setenv(SMS, "http://api:8000")
    monkeypatch.setattr(backend_config, "ping", lambda *a, **k: pytest.fail("pinged"))
    assert workspace_deps_views.remote_health() == {
        "configured": False, "base_url": None, "reachable": False, "version": None,
        "error": "no remote backend is configured"}


def test_remote_health_answers_a_broken_plugin(broken_plugin, monkeypatch):
    from vivarium_workbench.lib import workspace_deps_views

    monkeypatch.setattr(backend_config, "ping", lambda *a, **k: pytest.fail("pinged"))
    body = workspace_deps_views.remote_health()
    assert (body["configured"], body["base_url"], body["reachable"]) == (False, None, False)
    assert "some_dependency" in body["error"]


# --- a broken plugin at every process entry ---------------------------------------------------------------------------

def test_serve_fastapi_reports_a_broken_plugin(broken_plugin, capsys, tmp_path):
    from vivarium_workbench.lib import startup

    assert startup.serve_fastapi(tmp_path, port=0) == 2
    assert "the backend plugin failed to load: ImportError" in capsys.readouterr().err


_BROKEN_API_PROBE = textwrap.dedent('''
    import runpy, sys

    class _Broken:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "vivarium_workbench_sms.provider":
                raise ImportError("No module named 'some_dependency'", name="some_dependency")
            return None

    sys.meta_path.insert(0, _Broken())
    import uvicorn
    uvicorn.run = lambda *a, **k: print("@@SERVED@@")
    sys.argv = ["vivarium_workbench.api"]
    runpy.run_module("vivarium_workbench.api", run_name="__main__")
''')


def test_the_typed_api_entry_reports_a_broken_plugin():
    """``python -m vivarium_workbench.api`` with a plugin that fails to import: the message, not a traceback (the
    module no longer builds the app at import, which asked the providers before the check could)."""
    proc = subprocess.run([sys.executable, "-c", _BROKEN_API_PROBE], capture_output=True, text=True, timeout=300)
    assert proc.returncode != 0 and "@@SERVED@@" not in proc.stdout
    assert "ERROR: the backend plugin failed to load: ImportError: No module named 'some_dependency'" in proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr[-2000:]


def test_the_typed_api_entry_warns_about_a_stray_alias(providers_off, monkeypatch, capsys):
    import uvicorn

    from vivarium_workbench.api import __main__ as api_main

    served = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: served.append(1))
    monkeypatch.setattr(sys, "argv", ["vivarium_workbench.api"])
    monkeypatch.setenv(VIVA, "http://viva.test")
    api_main.main()
    assert served
    assert "warning: VIVA_API_BASE is set but no backend provider is active; it is ignored" in capsys.readouterr().err


def test_the_cli_reports_a_broken_plugin(broken_plugin, capsys, tmp_path):
    from vivarium_workbench import cli

    (tmp_path / "workspace.yaml").write_text("name: x\n")
    assert cli.main(["serve", "--workspace", str(tmp_path), "--detach"]) == 2
    assert "the backend plugin failed to load: ImportError" in capsys.readouterr().err


# --- the CLI's hint ---------------------------------------------------------------------------------------------------

def test_an_unknown_subcommand_with_no_provider_gets_the_hint(providers_off, capsys):
    from vivarium_workbench import cli

    with pytest.raises(SystemExit):
        cli.main(["run-remote", "x"])
    err = capsys.readouterr().err
    assert "invalid choice: 'run-remote'" in err and cli.NO_PROVIDER_HINT in err


def test_a_provider_that_failed_to_load_gets_the_hint(broken_plugin, capsys):
    from vivarium_workbench import cli

    with pytest.raises(SystemExit):
        cli.main(["run-remote", "x"])
    assert cli.NO_PROVIDER_HINT in capsys.readouterr().err


@pytest.mark.plugin
def test_no_hint_while_a_provider_adds_subcommands(capsys):
    import vivarium_workbench_sms  # noqa: F401  (a provider that adds subcommands)
    from vivarium_workbench import cli

    with pytest.raises(SystemExit):
        cli.main(["no-such-command"])
    err = capsys.readouterr().err
    assert "invalid choice" in err and cli.NO_PROVIDER_HINT not in err


# --- the plugin absent (a fresh interpreter, as an env worker built without it) -----------------------------------------

_ABSENT_PROBE = textwrap.dedent('''
    import json, os, sys

    class _NoPlugin:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "vivarium_workbench_sms" or fullname.startswith("vivarium_workbench_sms."):
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    sys.meta_path.insert(0, _NoPlugin())
    from vivarium_workbench.lib import backend_config
    from vivarium_workbench.lib.backends import config
    from vivarium_workbench.lib import workspace_deps_views

    backend_config.ping = lambda *a, **k: (_ for _ in ()).throw(AssertionError("pinged"))
    out = {"deployment": config.deployment_name(), "url": config.configured_base_url(),
           "configured": config.backend_configured(), "stray": config.stray_backend_env(),
           "names": sorted(config.deployment_names_now()), "health": workspace_deps_views.remote_health()}
    os.environ["VIVARIUM_WORKBENCH_BACKEND_BASE_URL"] = "http://compose.test"
    config.check_config()  # the alias beside it is not read
    out["named_url"] = config.configured_base_url()
    out["named_deployment"] = config.deployment_name()
    print("@@RESULT@@" + json.dumps(out))
''')


def test_the_plugin_absent_reads_only_the_named_url(monkeypatch):
    for var in (*backend_config.BACKEND_BASE_ENV_VARS, DEPLOY, "VIVARIUM_DASHBOARD_REMOTE_DEPLOYMENT",
                "VIVARIUM_DASHBOARD_BACKEND_BASE_URL", provider_mod.PROVIDERS_ENV):
        monkeypatch.delenv(var, raising=False)
    env = {**__import__("os").environ, SMS: "http://api:8000"}
    proc = subprocess.run([sys.executable, "-c", _ABSENT_PROBE], capture_output=True, text=True, timeout=300,
                          env=env)
    assert "@@RESULT@@" in proc.stdout, proc.stderr[-2000:]
    out = json.loads(proc.stdout.split("@@RESULT@@", 1)[1])
    assert out == {
        "deployment": "default", "url": None, "configured": False, "stray": [SMS], "names": ["default"],
        "health": {"configured": False, "base_url": None, "reachable": False, "version": None,
                   "error": "no remote backend is configured"},
        "named_url": "http://compose.test", "named_deployment": "compose.test",
    }

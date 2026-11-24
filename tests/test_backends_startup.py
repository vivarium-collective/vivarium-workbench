"""One remote backend per workbench process (docs/backend-adapters.md step 1c; Jim, 2026-11-19).

A backend named with ``--backend-base-url`` / ``VIVARIUM_WORKBENCH_BACKEND_BASE_URL`` must equal every alias that is
also set (``VIVA_API_BASE``, ``SMS_API_BASE``). The check reads the environment only and runs at process entry, for
the subcommands that can reach a backend; ``sms_api_base()`` itself never fails.

The aliases are the SMS plugin's (step P3 C1): with it absent they are ignored, so the cases that need an alias read
-- and the plugin's ``run-remote`` -- are marked ``plugin`` (docs/backend-adapters.md step P3, K2a).
"""
from __future__ import annotations

import argparse

import pytest

from vivarium_workbench import cli
from vivarium_workbench.lib import backend_config
from vivarium_workbench.lib.backends import config as backend_cfg

NAMED, VIVA, SMS = backend_config.BACKEND_BASE_ENV_VARS


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in backend_config.BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(v, raising=False)


def _alias_case(env, ok):
    """A case that needs the plugin's aliases (absent, they are ignored): marked ``plugin``."""
    return pytest.param(env, ok, marks=pytest.mark.plugin)


#: Every environment shape the start-up check must answer.
_ENVS = [
    ({}, True),
    ({SMS: "http://api:8000"}, True),                                   # Stanford
    ({VIVA: "http://a", SMS: "http://b"}, True),                        # aliases only: the first is the backend
    ({NAMED: "http://api:8000"}, True),
    ({NAMED: "http://api:8000", SMS: "http://api:8000"}, True),
    ({NAMED: "http://API:8000/", SMS: "http://api:8000"}, True),        # normalised: case, trailing slash
    _alias_case({NAMED: "http://compose.test", SMS: "http://api:8000"}, False),    # compose-api beside Stanford's SMS
    _alias_case({NAMED: "http://api:8000", VIVA: "http://api:8000", SMS: "http://other"}, False),  # every alias counts
    _alias_case({NAMED: "http://api:8000", VIVA: "http://other", SMS: "http://api:8000"}, False),
    _alias_case({NAMED: "http://api:8000", SMS: "not a url"}, False),
    ({NAMED: "not a url"}, False),
]


@pytest.mark.parametrize("env, ok", _ENVS)
def test_check_config(monkeypatch, env, ok):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if ok:
        backend_cfg.check_config()
    else:
        with pytest.raises(backend_config.BackendConfigError):
            backend_cfg.check_config()


@pytest.mark.plugin
def test_the_sms_base_url_never_raises_whatever_the_environment(monkeypatch):
    """The SMS client's ``sms_api_base()`` answers every environment the check refuses (split from
    ``test_check_config`` so the check itself runs core-only: docs/backend-adapters.md step P3, K2a)."""
    from vivarium_workbench_sms import sms_api_client as sac

    for case in _ENVS:
        env = case.values[0] if hasattr(case, "values") else case[0]
        with monkeypatch.context() as m:
            for k, v in env.items():
                m.setenv(k, v)
            sac.sms_api_base()


@pytest.mark.plugin
def test_the_flag_value_is_checked_before_it_reaches_the_environment(monkeypatch):
    monkeypatch.setenv(SMS, "http://api:8000")
    with pytest.raises(backend_config.BackendConfigError) as e:
        backend_cfg.check_config("http://compose.test")
    assert "SMS_API_BASE" in str(e.value) and "compose.test" in str(e.value)


def _subcommands() -> set[str]:
    parser = cli._build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return set(action.choices)


def test_every_subcommand_is_classified_exactly_once():
    """Core subcommands are in exactly one core set; a provider's (step P2 CLI) say for themselves
    (``Command.uses_backend``) and may not reuse a core name."""
    subs = _subcommands()
    provided = {c.name for c in cli._provider_commands()}
    assert not (cli.BACKEND_COMMANDS & cli.NO_BACKEND_COMMANDS)
    assert not (provided & (cli.BACKEND_COMMANDS | cli.NO_BACKEND_COMMANDS))
    assert subs == cli.BACKEND_COMMANDS | cli.NO_BACKEND_COMMANDS | provided, (
        "classify new subcommands in cli.BACKEND_COMMANDS or cli.NO_BACKEND_COMMANDS: "
        f"{sorted(subs ^ (cli.BACKEND_COMMANDS | cli.NO_BACKEND_COMMANDS | provided))}")


@pytest.mark.plugin
def test_run_remote_is_the_sms_providers_backend_command():
    """run-remote is the SMS provider's, and it reaches a backend: both start-up checks run before it (split from
    the classification test above so that one runs core-only: docs/backend-adapters.md step P3, K2a)."""
    provided = {c.name for c in cli._provider_commands()}
    assert "run-remote" in provided and "run-remote" not in cli.BACKEND_COMMANDS
    assert "run-remote" in cli._backend_commands(cli._provider_commands())


@pytest.mark.plugin
def test_a_provider_backend_command_gets_the_hosted_worker_check(monkeypatch, capsys, tmp_path):
    """The one backend-command set (core + ``uses_backend``) carries P2 Workers' check too."""
    from vivarium_workbench.lib import env_worker_launcher

    def refuse():
        raise backend_config.BackendConfigError("hosted env workers are asked for but no provider supplies them")

    monkeypatch.setattr(env_worker_launcher, "check_worker_config", refuse)
    assert cli.main(["run-remote", "--workspace", str(tmp_path), "some.composite"]) == 2
    assert "no provider supplies them" in capsys.readouterr().err


@pytest.mark.plugin
def test_a_backend_command_refuses_two_backends(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv(NAMED, "http://compose.test")
    monkeypatch.setenv(SMS, "http://api:8000")
    assert cli.main(["run-remote", "--workspace", str(tmp_path), "some.composite"]) == 2
    assert "two remote backends" in capsys.readouterr().err


def test_a_no_backend_command_is_not_stopped(monkeypatch, capsys, tmp_path):
    """A stale alias in the shell must not stop a command that never resolves a backend."""
    monkeypatch.setenv(NAMED, "http://compose.test")
    monkeypatch.setenv(SMS, "http://api:8000")
    ran = []
    monkeypatch.setattr(cli, "cmd_gen_readme", lambda args: ran.append(args) or 0)
    assert cli.main(["gen-readme", "--workspace", str(tmp_path)]) == 0
    assert ran and "two remote backends" not in capsys.readouterr().err


@pytest.mark.plugin
def test_serve_detach_refuses_with_the_message_before_detaching(monkeypatch, capsys, tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: x\n")
    monkeypatch.setenv(SMS, "http://api:8000")
    called = []
    monkeypatch.setattr(cli, "_serve_detached", lambda *a, **k: called.append(1) or 0)
    rc = cli.main(["serve", "--workspace", str(tmp_path), "--detach", "--backend-base-url", "http://compose.test"])
    assert rc == 2 and not called
    assert "two remote backends" in capsys.readouterr().err


@pytest.mark.plugin
def test_serve_fastapi_refuses_two_backends_set_directly_in_the_environment(monkeypatch, capsys, tmp_path):
    from vivarium_workbench.lib import startup

    monkeypatch.setenv(NAMED, "http://compose.test")
    monkeypatch.setenv(SMS, "http://api:8000")
    assert startup.serve_fastapi(tmp_path, port=0) == 2
    assert "two remote backends" in capsys.readouterr().err


# The alias variant needs a provider to claim the alias: with none installed, serve refuses it (step P4b2).
@pytest.mark.parametrize("alias", [pytest.param(True, marks=pytest.mark.plugin, id="alias"),
                                   pytest.param(False, id="no-alias")])
def test_serves_flag_replaces_a_stale_named_env_var(monkeypatch, tmp_path, alias):
    """``serve --backend-base-url B`` with a stale ``VIVARIUM_WORKBENCH_BACKEND_BASE_URL=A`` (and, with a provider, an
    alias of B) is one backend: the flag is what that process uses."""
    (tmp_path / "workspace.yaml").write_text("name: x\n")
    if alias:
        monkeypatch.setenv(NAMED, "http://stale.test")
        monkeypatch.setenv(SMS, "http://api:8000")
    else:  # core-only: a stale value the core itself would refuse, so the flag must be what is checked
        monkeypatch.setenv(NAMED, "not a url")
        monkeypatch.delenv(SMS, raising=False)
        monkeypatch.delenv(VIVA, raising=False)
    called = []
    monkeypatch.setattr(cli, "_serve_detached", lambda *a, **k: called.append(1) or 0)
    assert cli.main(["serve", "--workspace", str(tmp_path), "--detach", "--backend-base-url", "http://api:8000"]) == 0
    assert called


class _Stop(BaseException):
    """Ends ``serve_fastapi`` right after the start-up probes (``except Exception`` there does not catch it)."""


@pytest.mark.parametrize("env, expected", [_alias_case({SMS: "http://api:8000"}, ["http://api:8000"]), ({}, [])])
def test_serve_starts_the_breaker_probe_on_the_configured_url(monkeypatch, tmp_path, env, expected):
    """Step P2: the startup probe is core config (``backend_config.start_probe(backends.config.configured_base_url())``), the URL
    ``remote_link.start_probe()`` used; only when a backend is configured."""
    from vivarium_workbench.lib import _root, backend_config, startup, workspace_deps_views

    for k, v in env.items():
        monkeypatch.setenv(k, v)
    started = []
    monkeypatch.setattr(backend_config, "start_probe", lambda url: started.append(url))
    monkeypatch.setattr(workspace_deps_views, "remote_health",
                        lambda: {"reachable": True, "base_url": "x", "version": "1"})

    def stop(_ws):
        raise _Stop

    monkeypatch.setattr(_root, "set_workspace_root", stop)
    with pytest.raises(_Stop):
        startup.serve_fastapi(tmp_path, port=0)
    assert started == expected

"""One remote backend per workbench process (docs/backend-adapters.md step 1c; Jim, 2026-11-19).

A backend named with ``--backend-base-url`` / ``VIVARIUM_WORKBENCH_BACKEND_BASE_URL`` must equal every alias that is
also set (``VIVA_API_BASE``, ``SMS_API_BASE``). The check reads the environment only and runs at process entry, for
the subcommands that can reach a backend; ``sms_api_base()`` itself never fails.
"""
from __future__ import annotations

import argparse

import pytest

from vivarium_workbench import cli
from vivarium_workbench.lib import sms_api_client as sac

NAMED, VIVA, SMS = sac.BACKEND_BASE_ENV_VARS


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in sac.BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(v, raising=False)


@pytest.mark.parametrize("env, ok", [
    ({}, True),
    ({SMS: "http://api:8000"}, True),                                   # Stanford
    ({VIVA: "http://a", SMS: "http://b"}, True),                        # aliases only: the first is the backend
    ({NAMED: "http://api:8000"}, True),
    ({NAMED: "http://api:8000", SMS: "http://api:8000"}, True),
    ({NAMED: "http://API:8000/", SMS: "http://api:8000"}, True),        # normalised: case, trailing slash
    ({NAMED: "http://compose.test", SMS: "http://api:8000"}, False),    # compose-api beside Stanford's SMS
    ({NAMED: "http://api:8000", VIVA: "http://api:8000", SMS: "http://other"}, False),  # every set alias counts
    ({NAMED: "http://api:8000", VIVA: "http://other", SMS: "http://api:8000"}, False),
    ({NAMED: "http://api:8000", SMS: "not a url"}, False),
    ({NAMED: "not a url"}, False),
])
def test_check_single_backend(monkeypatch, env, ok):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if ok:
        sac.check_single_backend()
    else:
        with pytest.raises(sac.BackendConfigError):
            sac.check_single_backend()
    sac.sms_api_base()  # never raises, whatever the environment


def test_the_flag_value_is_checked_before_it_reaches_the_environment(monkeypatch):
    monkeypatch.setenv(SMS, "http://api:8000")
    with pytest.raises(sac.BackendConfigError) as e:
        sac.check_single_backend("http://compose.test")
    assert "SMS_API_BASE" in str(e.value) and "compose.test" in str(e.value)


def _subcommands() -> set[str]:
    parser = cli._build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return set(action.choices)


def test_every_subcommand_is_classified_exactly_once():
    subs = _subcommands()
    assert not (cli.BACKEND_COMMANDS & cli.NO_BACKEND_COMMANDS)
    assert subs == cli.BACKEND_COMMANDS | cli.NO_BACKEND_COMMANDS, (
        "classify new subcommands in cli.BACKEND_COMMANDS or cli.NO_BACKEND_COMMANDS: "
        f"{sorted(subs ^ (cli.BACKEND_COMMANDS | cli.NO_BACKEND_COMMANDS))}")


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


def test_serve_detach_refuses_with_the_message_before_detaching(monkeypatch, capsys, tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: x\n")
    monkeypatch.setenv(SMS, "http://api:8000")
    called = []
    monkeypatch.setattr(cli, "_serve_detached", lambda *a, **k: called.append(1) or 0)
    rc = cli.main(["serve", "--workspace", str(tmp_path), "--detach", "--backend-base-url", "http://compose.test"])
    assert rc == 2 and not called
    assert "two remote backends" in capsys.readouterr().err


def test_serve_fastapi_refuses_two_backends_set_directly_in_the_environment(monkeypatch, capsys, tmp_path):
    from vivarium_workbench.lib import startup

    monkeypatch.setenv(NAMED, "http://compose.test")
    monkeypatch.setenv(SMS, "http://api:8000")
    assert startup.serve_fastapi(tmp_path, port=0) == 2
    assert "two remote backends" in capsys.readouterr().err


def test_serves_flag_replaces_a_stale_named_env_var(monkeypatch, tmp_path):
    """``serve --backend-base-url B`` with a stale ``VIVARIUM_WORKBENCH_BACKEND_BASE_URL=A`` and an alias of B is
    one backend: the flag is what that process uses."""
    (tmp_path / "workspace.yaml").write_text("name: x\n")
    monkeypatch.setenv(NAMED, "http://stale.test")
    monkeypatch.setenv(SMS, "http://api:8000")
    called = []
    monkeypatch.setattr(cli, "_serve_detached", lambda *a, **k: called.append(1) or 0)
    assert cli.main(["serve", "--workspace", str(tmp_path), "--detach", "--backend-base-url", "http://api:8000"]) == 0
    assert called

"""C4: Tests for the 'run-remote' CLI subcommand.

Tests:
- run-remote subcommand exists
- Dirty/unpushed git guard raises before any submission
- The command calls compose_submit
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers: create git repos for testing
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _make_clean_git_repo(tmp_path: Path) -> Path:
    """Create a clean git repo with a remote and a committed file."""
    repo = tmp_path / "ws"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")

    # Create a fake remote
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(
        ["git", "-C", str(remote), "init", "--bare"],
        check=True, capture_output=True,
    )
    _git(repo, "remote", "add", "origin", str(remote))

    # Create a workspace.yaml so it's recognized as a workspace
    (repo / "workspace.yaml").write_text("name: test-ws\n")
    _git(repo, "add", "workspace.yaml")
    _git(repo, "commit", "-m", "init")
    _git(repo, "push", "-u", "origin", "HEAD:main")
    return repo


def _make_dirty_git_repo(tmp_path: Path) -> Path:
    """Create a repo with uncommitted changes."""
    repo = _make_clean_git_repo(tmp_path)
    (repo / "dirty_file.txt").write_text("uncommitted change")
    return repo


def _make_unpushed_git_repo(tmp_path: Path) -> Path:
    """Create a repo where HEAD is not pushed to remote."""
    repo = _make_clean_git_repo(tmp_path)
    (repo / "new_file.txt").write_text("new content")
    _git(repo, "add", "new_file.txt")
    _git(repo, "commit", "-m", "unpushed commit")
    # NOT pushed — HEAD is ahead of origin
    return repo


# ---------------------------------------------------------------------------
# Tests for git_pip_url / dirty tree guard
# ---------------------------------------------------------------------------

def test_run_remote_module_importable():
    """remote_run module must be importable."""
    from vivarium_workbench.lib import remote_run  # noqa: F401


def test_git_pip_url_raises_for_dirty_tree(tmp_path):
    """git_pip_url raises RuntimeError when the git working tree is dirty."""
    from vivarium_workbench.lib.remote_run import git_pip_url

    repo = _make_dirty_git_repo(tmp_path)
    with pytest.raises(RuntimeError, match="uncommitted|dirty|untracked"):
        git_pip_url(repo)


def test_git_pip_url_raises_for_unpushed_commits(tmp_path):
    """git_pip_url raises RuntimeError when HEAD is ahead of remote (unpushed)."""
    from vivarium_workbench.lib.remote_run import git_pip_url

    repo = _make_unpushed_git_repo(tmp_path)
    with pytest.raises(RuntimeError, match="unpushed|not pushed|ahead"):
        git_pip_url(repo)


def test_git_pip_url_returns_git_url_for_clean_pushed_repo(tmp_path):
    """git_pip_url returns a git+<origin>@<sha> URL for a clean pushed repo."""
    from vivarium_workbench.lib.remote_run import git_pip_url

    repo = _make_clean_git_repo(tmp_path)
    url = git_pip_url(repo)
    assert url.startswith("git+")
    assert "@" in url  # <sha> is appended after @


# ---------------------------------------------------------------------------
# Tests for run_remote orchestration
# ---------------------------------------------------------------------------

def test_run_remote_calls_compose_submit(tmp_path):
    """run_remote calls compose_submit with the .pbg content."""
    from vivarium_workbench.lib.remote_run import run_remote

    # Set up a clean repo with a workspace
    repo = _make_clean_git_repo(tmp_path)

    # Mock client
    mock_client = MagicMock()
    mock_client.compose_submit.return_value = 77
    mock_client.compose_status.return_value = {"status": "completed"}
    # Set up binary results.zip
    results_zip = tmp_path / "fake_results.zip"
    results_zip.write_bytes(b"PK\x03\x04fake")
    mock_client.download_compose_results.return_value = results_zip

    # Mock export_composite_pbg to write a fake .pbg file
    def fake_export(ws_root, composite_id, out_path, core=None, overrides=None):
        Path(out_path).write_text('{"state": {}, "schema": {}}')
        return Path(out_path)

    with patch("vivarium_workbench.lib.remote_run.export_composite_pbg", side_effect=fake_export), \
         patch("vivarium_workbench.lib.remote_run.git_pip_url", return_value="git+file:///r@abc123"):
        result = run_remote(repo, "test-composite", client=mock_client, skip_preflight=True)

    mock_client.compose_submit.assert_called_once()
    call_kwargs = mock_client.compose_submit.call_args
    # First arg should be bytes of the .pbg
    pbg_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("pbg_bytes")
    assert isinstance(pbg_arg, bytes)


def test_run_remote_passes_git_url_as_extra_dep(tmp_path):
    """run_remote passes the workspace git URL as extra_pip_deps to compose_submit."""
    from vivarium_workbench.lib.remote_run import run_remote

    repo = _make_clean_git_repo(tmp_path)
    mock_client = MagicMock()
    mock_client.compose_submit.return_value = 10
    mock_client.compose_status.return_value = {"status": "completed"}
    results_zip = tmp_path / "fake_results.zip"
    results_zip.write_bytes(b"PK")
    mock_client.download_compose_results.return_value = results_zip

    git_url = "git+file:///some/path@deadbeef"

    def fake_export(ws_root, composite_id, out_path, core=None, overrides=None):
        Path(out_path).write_text('{"state": {}, "schema": {}}')
        return Path(out_path)

    with patch("vivarium_workbench.lib.remote_run.export_composite_pbg", side_effect=fake_export), \
         patch("vivarium_workbench.lib.remote_run.git_pip_url", return_value=git_url):
        run_remote(repo, "test-composite", client=mock_client, skip_preflight=True)

    call_kwargs = mock_client.compose_submit.call_args
    # extra_pip_deps should contain the git url
    if call_kwargs[1]:
        deps = call_kwargs[1].get("extra_pip_deps", [])
    else:
        deps = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else []
    assert git_url in deps, f"Expected {git_url!r} in extra_pip_deps, got: {deps}"


def test_run_remote_returns_results_path(tmp_path):
    """run_remote returns the path to the downloaded results.zip."""
    from vivarium_workbench.lib.remote_run import run_remote

    repo = _make_clean_git_repo(tmp_path)
    mock_client = MagicMock()
    mock_client.compose_submit.return_value = 5
    mock_client.compose_status.return_value = {"status": "completed"}
    expected_path = tmp_path / "results.zip"
    expected_path.write_bytes(b"PK")
    mock_client.download_compose_results.return_value = expected_path

    def fake_export(ws_root, composite_id, out_path, core=None, overrides=None):
        Path(out_path).write_text('{"state": {}, "schema": {}}')
        return Path(out_path)

    with patch("vivarium_workbench.lib.remote_run.export_composite_pbg", side_effect=fake_export), \
         patch("vivarium_workbench.lib.remote_run.git_pip_url", return_value="git+file:///r@sha"):
        result = run_remote(repo, "test-composite", client=mock_client, skip_preflight=True)

    assert result == expected_path


# ---------------------------------------------------------------------------
# Tests for CLI subcommand registration
# ---------------------------------------------------------------------------

def test_run_remote_subcommand_exists_in_cli():
    """The 'run-remote' subcommand must be registered in cli.py."""
    from vivarium_workbench import cli

    # Parse with --help should show run-remote exists (argparse raises SystemExit on --help)
    with pytest.raises(SystemExit):
        cli.main(["run-remote", "--help"])


def test_run_remote_cli_accepts_workspace_and_composite(tmp_path, monkeypatch):
    """run-remote CLI accepts --workspace and positional composite argument.

    The handler is the SMS plugin's (step P2 CLI), patched on its module: the provider builds its commands fresh on
    every call, so the parser binds the patched function."""
    from vivarium_workbench import cli

    repo = _make_clean_git_repo(tmp_path)
    called = {}

    def fake_cmd_run_remote(args):
        called["args"] = args
        return 0

    monkeypatch.setattr("vivarium_workbench_sms.cli.cmd_run_remote", fake_cmd_run_remote)
    assert cli.main(["run-remote", "--workspace", str(repo), "test-composite"]) == 0
    assert called["args"].workspace == str(repo) and called["args"].composite == "test-composite"


# ---------------------------------------------------------------------------
# Step P2 CLI: run-remote is the SMS provider's subcommand, unchanged
# ---------------------------------------------------------------------------

def _subparsers(parser):
    import argparse

    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


def test_run_remote_golden_namespace_and_slot():
    """The arguments, defaults and position in --help are what they were in vivarium_workbench/cli.py."""
    from vivarium_workbench import cli
    from vivarium_workbench_sms import cli as sms_cli

    parser = cli._build_parser()
    ns = parser.parse_args(["run-remote", "--workspace", "w", "c", "--sms-api-url", "u", "--poll-interval", "2",
                            "--dest", "d"])
    assert ns.func is sms_cli.cmd_run_remote
    got = {k: v for k, v in vars(ns).items() if k != "func"}
    assert got == {"cmd": "run-remote", "workspace": "w", "composite": "c", "sms_api_url": "u",
                   "poll_interval": 2.0, "dest": "d"}
    defaults = {k: v for k, v in vars(parser.parse_args(["run-remote", "c"])).items() if k != "func"}
    assert defaults == {"cmd": "run-remote", "workspace": ".", "composite": "c", "sms_api_url": None,
                        "poll_interval": 10.0, "dest": None}
    names = list(_subparsers(parser).choices)
    assert names[names.index("prepare-investigation") + 1] == "run-remote"
    helps = {a.dest: a.help for a in _subparsers(parser)._choices_actions}
    assert helps["run-remote"] == "Export a composite and run it on sms-api (requires pushed git tree)"


class _Recorder:
    def __init__(self):
        self.base_urls = []


@pytest.fixture
def _handler(monkeypatch, tmp_path):
    """The real handler with ``run_remote`` stubbed and the client's base URL recorded."""
    from vivarium_workbench_sms import sms_api_client as sac

    for var in sac.BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.replace("VIVARIUM_WORKBENCH_", "VIVARIUM_DASHBOARD_"), raising=False)
    rec = _Recorder()
    real_init = sac.SmsApiClient.__init__

    def init(self, base_url="http://localhost:8080", *a, **k):
        rec.base_urls.append(base_url)
        real_init(self, base_url, *a, **k)

    monkeypatch.setattr(sac.SmsApiClient, "__init__", init)
    rec.outcome = lambda *a, **k: tmp_path / "results.zip"
    monkeypatch.setattr("vivarium_workbench.lib.remote_run.run_remote", lambda *a, **k: rec.outcome(*a, **k))
    (tmp_path / "workspace.yaml").write_text("name: x\n")
    rec.ws = tmp_path
    return rec


def test_run_remote_handler_url_precedence(_handler, monkeypatch, capsys):
    from vivarium_workbench import cli

    assert cli.main(["run-remote", "--workspace", str(_handler.ws), "c", "--sms-api-url", "http://flag:1"]) == 0
    monkeypatch.setenv("SMS_API_BASE", "http://env:2")
    assert cli.main(["run-remote", "--workspace", str(_handler.ws), "c", "--sms-api-url", "http://flag:1"]) == 0
    assert cli.main(["run-remote", "--workspace", str(_handler.ws), "c"]) == 0
    monkeypatch.delenv("SMS_API_BASE")
    assert cli.main(["run-remote", "--workspace", str(_handler.ws), "c"]) == 0
    assert _handler.base_urls == ["http://flag:1", "http://flag:1", "http://env:2", "http://localhost:8080"]
    assert "Done. Results:" in capsys.readouterr().out


def test_run_remote_handler_exit_codes(_handler, capsys, tmp_path_factory):
    from vivarium_workbench import cli
    from vivarium_workbench_sms.sms_api_client import SmsApiError

    def runtime(*a, **k):
        raise RuntimeError("tree is dirty")

    _handler.outcome = runtime
    assert cli.main(["run-remote", "--workspace", str(_handler.ws), "c"]) == 1
    assert "ERROR: tree is dirty" in capsys.readouterr().err

    def sms(*a, **k):
        raise SmsApiError("GET x -> 500", status=500)

    _handler.outcome = sms
    assert cli.main(["run-remote", "--workspace", str(_handler.ws), "c"]) == 1
    assert "ERROR (sms-api): GET x -> 500" in capsys.readouterr().err

    not_ws = tmp_path_factory.mktemp("not-a-workspace")
    assert cli.main(["run-remote", "--workspace", str(not_ws), "c"]) == 2
    assert "not a workspace" in capsys.readouterr().err


@pytest.fixture
def _providers_off(monkeypatch):
    from vivarium_workbench.lib import backends
    from vivarium_workbench.lib.backends import provider as provider_mod

    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    yield
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()


def test_with_providers_off_run_remote_does_not_exist(_providers_off, capsys):
    from vivarium_workbench import cli

    with pytest.raises(SystemExit) as e:
        cli.main(["run-remote", "c"])
    assert e.value.code == 2
    assert "invalid choice: 'run-remote'" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "run-remote" not in capsys.readouterr().out
    assert "run-remote" not in cli._backend_commands(cli._provider_commands())


def test_a_broken_provider_drops_its_subcommands_with_a_warning(monkeypatch, capsys, tmp_path):
    from vivarium_workbench import cli
    from vivarium_workbench.lib.backends import provider as provider_mod

    def boom():
        raise ImportError("boom")

    monkeypatch.setattr(provider_mod, "providers", boom)
    ran = []
    monkeypatch.setattr(cli, "cmd_gen_readme", lambda args: ran.append(args) or 0)
    assert cli.main(["gen-readme", "--workspace", str(tmp_path)]) == 0
    assert ran
    assert "backend provider failed to load (ImportError: boom)" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        cli.main(["run-remote", "c"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "ImportError: boom" in err and "invalid choice: 'run-remote'" in err


def test_a_provider_subcommand_may_not_shadow_a_core_one():
    from vivarium_workbench import cli
    from vivarium_workbench.lib.backends.provider import Command

    clash = Command("serve", help="x", configure=lambda p: None, run=lambda a: 0, uses_backend=False)
    with pytest.raises(RuntimeError, match="collides with a core subcommand"):
        cli._build_parser((clash,))
    late = Command("sync", help="x", configure=lambda p: None, run=lambda a: 0, uses_backend=False)
    with pytest.raises(RuntimeError, match="collides with a core subcommand"):
        cli._build_parser((late,))

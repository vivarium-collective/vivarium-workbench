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
    from vivarium_dashboard.lib import remote_run  # noqa: F401


def test_git_pip_url_raises_for_dirty_tree(tmp_path):
    """git_pip_url raises RuntimeError when the git working tree is dirty."""
    from vivarium_dashboard.lib.remote_run import git_pip_url

    repo = _make_dirty_git_repo(tmp_path)
    with pytest.raises(RuntimeError, match="uncommitted|dirty|untracked"):
        git_pip_url(repo)


def test_git_pip_url_raises_for_unpushed_commits(tmp_path):
    """git_pip_url raises RuntimeError when HEAD is ahead of remote (unpushed)."""
    from vivarium_dashboard.lib.remote_run import git_pip_url

    repo = _make_unpushed_git_repo(tmp_path)
    with pytest.raises(RuntimeError, match="unpushed|not pushed|ahead"):
        git_pip_url(repo)


def test_git_pip_url_returns_git_url_for_clean_pushed_repo(tmp_path):
    """git_pip_url returns a git+<origin>@<sha> URL for a clean pushed repo."""
    from vivarium_dashboard.lib.remote_run import git_pip_url

    repo = _make_clean_git_repo(tmp_path)
    url = git_pip_url(repo)
    assert url.startswith("git+")
    assert "@" in url  # <sha> is appended after @


# ---------------------------------------------------------------------------
# Tests for run_remote orchestration
# ---------------------------------------------------------------------------

def test_run_remote_calls_compose_submit(tmp_path):
    """run_remote calls compose_submit with the .pbg content."""
    from vivarium_dashboard.lib.remote_run import run_remote

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
    def fake_export(ws_root, composite_id, out_path, core=None):
        Path(out_path).write_text('{"state": {}, "schema": {}}')
        return Path(out_path)

    with patch("vivarium_dashboard.lib.remote_run.export_composite_pbg", side_effect=fake_export), \
         patch("vivarium_dashboard.lib.remote_run.git_pip_url", return_value="git+file:///r@abc123"):
        result = run_remote(repo, "test-composite", client=mock_client)

    mock_client.compose_submit.assert_called_once()
    call_kwargs = mock_client.compose_submit.call_args
    # First arg should be bytes of the .pbg
    pbg_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("pbg_bytes")
    assert isinstance(pbg_arg, bytes)


def test_run_remote_passes_git_url_as_extra_dep(tmp_path):
    """run_remote passes the workspace git URL as extra_pip_deps to compose_submit."""
    from vivarium_dashboard.lib.remote_run import run_remote

    repo = _make_clean_git_repo(tmp_path)
    mock_client = MagicMock()
    mock_client.compose_submit.return_value = 10
    mock_client.compose_status.return_value = {"status": "completed"}
    results_zip = tmp_path / "fake_results.zip"
    results_zip.write_bytes(b"PK")
    mock_client.download_compose_results.return_value = results_zip

    git_url = "git+file:///some/path@deadbeef"

    def fake_export(ws_root, composite_id, out_path, core=None):
        Path(out_path).write_text('{"state": {}, "schema": {}}')
        return Path(out_path)

    with patch("vivarium_dashboard.lib.remote_run.export_composite_pbg", side_effect=fake_export), \
         patch("vivarium_dashboard.lib.remote_run.git_pip_url", return_value=git_url):
        run_remote(repo, "test-composite", client=mock_client)

    call_kwargs = mock_client.compose_submit.call_args
    # extra_pip_deps should contain the git url
    if call_kwargs[1]:
        deps = call_kwargs[1].get("extra_pip_deps", [])
    else:
        deps = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else []
    assert git_url in deps, f"Expected {git_url!r} in extra_pip_deps, got: {deps}"


def test_run_remote_returns_results_path(tmp_path):
    """run_remote returns the path to the downloaded results.zip."""
    from vivarium_dashboard.lib.remote_run import run_remote

    repo = _make_clean_git_repo(tmp_path)
    mock_client = MagicMock()
    mock_client.compose_submit.return_value = 5
    mock_client.compose_status.return_value = {"status": "completed"}
    expected_path = tmp_path / "results.zip"
    expected_path.write_bytes(b"PK")
    mock_client.download_compose_results.return_value = expected_path

    def fake_export(ws_root, composite_id, out_path, core=None):
        Path(out_path).write_text('{"state": {}, "schema": {}}')
        return Path(out_path)

    with patch("vivarium_dashboard.lib.remote_run.export_composite_pbg", side_effect=fake_export), \
         patch("vivarium_dashboard.lib.remote_run.git_pip_url", return_value="git+file:///r@sha"):
        result = run_remote(repo, "test-composite", client=mock_client)

    assert result == expected_path


# ---------------------------------------------------------------------------
# Tests for CLI subcommand registration
# ---------------------------------------------------------------------------

def test_run_remote_subcommand_exists_in_cli():
    """The 'run-remote' subcommand must be registered in cli.py."""
    from vivarium_dashboard import cli

    # Parse with --help should show run-remote exists (argparse raises SystemExit on --help)
    with pytest.raises(SystemExit):
        cli.main(["run-remote", "--help"])


def test_run_remote_cli_accepts_workspace_and_composite(tmp_path):
    """run-remote CLI accepts --workspace and positional composite argument."""
    from vivarium_dashboard import cli

    # Use a parser-level check: just verify no "unrecognized argument" error
    # by checking the argparse namespace (don't actually run the submission)
    repo = _make_clean_git_repo(tmp_path)

    called = {}

    def fake_cmd_run_remote(args):
        called["args"] = args
        return 0

    original = getattr(cli, "cmd_run_remote", None)
    cli_module = sys.modules["vivarium_dashboard.cli"]
    # Patch cmd_run_remote to avoid real execution
    import vivarium_dashboard.cli as cli_mod
    original_cmd = getattr(cli_mod, "cmd_run_remote", None)
    cli_mod.cmd_run_remote = fake_cmd_run_remote
    try:
        ret = cli_mod.main(["run-remote", "--workspace", str(repo), "test-composite"])
    except Exception:
        ret = None
    finally:
        if original_cmd is not None:
            cli_mod.cmd_run_remote = original_cmd
    # Either it ran (ret=0) or called our fake
    assert called or ret == 0

"""``vivarium-workbench workspace-trust``: list and revoke the workspaces trusted to run chat commands."""
import os

import pytest

from vivarium_workbench.cli import main
from vivarium_workbench.lib import user_state


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_CONFIG_DIR", str(tmp_path / "config"))
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def test_nothing_trusted_says_so(cfg, capsys):
    assert main(["workspace-trust", "--list"]) == 0
    assert "No workspace is trusted" in capsys.readouterr().out


def test_list_shows_every_trusted_workspace_by_real_path(cfg, tmp_path, capsys):
    other = tmp_path / "other"
    other.mkdir()
    user_state.grant_trust(cfg)
    user_state.grant_trust(other)
    assert main(["workspace-trust", "--list"]) == 0
    out = capsys.readouterr().out
    assert os.path.realpath(cfg) in out and os.path.realpath(other) in out and out.count("trusted 20") == 2


def test_revoke_removes_only_that_workspace_and_it_is_no_longer_trusted(cfg, tmp_path, capsys):
    other = tmp_path / "other"
    other.mkdir()
    user_state.grant_trust(cfg)
    user_state.grant_trust(other)
    assert main(["workspace-trust", "--revoke", str(cfg)]) == 0
    assert "revoked" in capsys.readouterr().out
    assert not user_state.is_trusted(cfg) and user_state.is_trusted(other)
    assert [e["path"] for e in user_state.list_trusted()] == [os.path.realpath(other)]


def test_revoke_works_through_a_symlink_alias_and_a_relative_path(cfg, tmp_path, monkeypatch):
    user_state.grant_trust(cfg)
    alias = tmp_path / "alias"
    alias.symlink_to(cfg)
    assert main(["workspace-trust", "--revoke", str(alias)]) == 0
    assert not user_state.is_trusted(cfg)
    user_state.grant_trust(cfg)
    monkeypatch.chdir(cfg.parent)
    assert main(["workspace-trust", "--revoke", "ws"]) == 0
    assert not user_state.is_trusted(cfg)


def test_revoking_an_untrusted_path_reports_it_and_exits_nonzero(cfg, capsys):
    assert main(["workspace-trust", "--revoke", str(cfg)]) == 1
    assert "not trusted" in capsys.readouterr().err


def test_one_of_list_or_revoke_is_required_and_they_exclude_each_other(cfg):
    for argv in (["workspace-trust"], ["workspace-trust", "--list", "--revoke", "x"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2

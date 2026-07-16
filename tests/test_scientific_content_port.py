"""Tests for the ScientificContent port + local-git adapter (PR-3, read surface)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from vivarium_workbench.lib import git_status
from vivarium_workbench.lib.adapters import scientific_content as adapter
from vivarium_workbench.lib.adapters.scientific_content import LocalGitScientificContent
from vivarium_workbench.lib.ports.scientific_content import ScientificContent


def test_for_workspace_returns_scientific_content(tmp_path: Path):
    rec = adapter.for_workspace(tmp_path)
    assert isinstance(rec, ScientificContent)          # runtime_checkable Protocol
    assert isinstance(rec, LocalGitScientificContent)


def test_read_methods_delegate_to_git_status(tmp_path: Path, monkeypatch):
    """Behavior-preserving: each read verb delegates verbatim, passing ws_root."""
    calls: dict[str, Path] = {}

    def _stub(name: str, ret: dict):
        def f(ws):
            calls[name] = ws
            return ret
        return f

    monkeypatch.setattr(git_status, "build_git_status", _stub("status", {"k": "status"}))
    monkeypatch.setattr(git_status, "build_work_status", _stub("work", {"k": "work"}))
    monkeypatch.setattr(git_status, "build_dirty_status", _stub("dirty", {"k": "dirty"}))

    rec = LocalGitScientificContent(tmp_path)
    assert rec.status() == {"k": "status"}
    assert rec.work_status() == {"k": "work"}
    assert rec.dirty_status() == {"k": "dirty"}
    assert calls == {"status": tmp_path, "work": tmp_path, "dirty": tmp_path}


def test_head_version_in_a_real_repo(tmp_path: Path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")

    head = LocalGitScientificContent(tmp_path).head_version()
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    assert head == expected and len(head) == 40


def test_head_version_empty_outside_a_repo(tmp_path: Path):
    assert LocalGitScientificContent(tmp_path).head_version() == ""

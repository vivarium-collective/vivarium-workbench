"""Tests for ``find_pbg_template`` and the git clone cache fallback.

Covers the three-resolution chain: env override, sibling checkout, and
the ``~/.cache/vivarium-dashboard/pbg-template/`` git clone fallback that
is automatically performed when no local checkout exists.

(Phase G of todo #8.)
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone

import pytest

from vivarium_dashboard.lib.workspace_create import (
    find_pbg_template,
    WorkspaceCreateError,
)


def test_uses_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "tpl"
    fake.mkdir()
    (fake / "template-init.sh").write_text("#!/bin/sh\necho ok\n")
    monkeypatch.setenv("PBG_TEMPLATE_PATH", str(fake))
    assert find_pbg_template() == fake


def test_env_override_can_point_at_parent(monkeypatch, tmp_path):
    root = tmp_path / "pbg-template"
    (root / "template").mkdir(parents=True)
    (root / "template" / "template-init.sh").write_text("#!/bin/sh\n")
    monkeypatch.setenv("PBG_TEMPLATE_PATH", str(root))
    assert find_pbg_template() == root / "template"


def test_sibling_discovery(monkeypatch, tmp_path):
    """Simulate a sibling pbg-template checkout by placing one in the parent
    of a fake vivarium-dashboard repo root."""
    repo_root = tmp_path / "vivarium-dashboard"
    repo_root.mkdir()
    monkeypatch.setattr(
        "vivarium_dashboard.lib.workspace_create.__file__",
        str(repo_root / "vivarium_dashboard" / "lib" / "workspace_create.py"),
    )
    sibling = tmp_path / "pbg-template" / "template"
    sibling.mkdir(parents=True)
    (sibling / "template-init.sh").write_text("#!/bin/sh\n")
    # Ensure no env override steals the show.
    monkeypatch.delenv("PBG_TEMPLATE_PATH", raising=False)
    # Isolate the cache directory so it can't accidentally match.
    monkeypatch.setenv("HOME", str(tmp_path / "no-cache"))
    assert find_pbg_template() == sibling


def test_cache_clone_fallback(monkeypatch, tmp_path):
    """When no env override or sibling exists, the function clones the
    pbg-template repo into the cache directory."""
    monkeypatch.delenv("PBG_TEMPLATE_PATH", raising=False)
    # Isolate HOME so ~/.cache is under tmp_path.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    # Monkeypatch the repo URL to a local bare repo we control.
    bare = tmp_path / "fake-remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", str(bare)],
                   capture_output=True, check=True)
    # Create an orphan ref to satisfy the clone.
    runner = tmp_path / "runner"
    subprocess.run(["git", "init", str(runner)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(runner), "checkout", "--orphan", "dynamic-workspace-images"],
                   capture_output=True, check=True)
    (runner / "template").mkdir(parents=True)
    (runner / "template" / "template-init.sh").write_text("#!/bin/sh\n")
    (runner / "template" / "workspace.yaml").write_text("name: stub\n")
    subprocess.run(["git", "-C", str(runner), "add", "-A"],
                   capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(runner), "commit", "-m", "stub",
         "--author=Test <test@test>"],
        capture_output=True, check=True, env={**os.environ,
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    subprocess.run(
        ["git", "-C", str(runner), "push", str(bare),
         "dynamic-workspace-images:refs/heads/dynamic-workspace-images"],
        capture_output=True, check=True,
    )

    from vivarium_dashboard.lib import workspace_create as wc_mod
    monkeypatch.setattr(wc_mod, "_PBG_TEMPLATE_REPO", str(bare))

    result = find_pbg_template()
    assert result.is_dir()
    assert (result / "template-init.sh").is_file()


def test_cache_clone_raises_when_no_git(monkeypatch, tmp_path):
    """If git is not available and no template exists, the function raises."""
    monkeypatch.delenv("PBG_TEMPLATE_PATH", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    # Isolate from the real sibling pbg-template checkout by redirecting
    # __file__ into tmp_path — without this, find_pbg_template() finds the
    # sibling and returns before reaching the clone/error path.
    from vivarium_dashboard.lib import workspace_create as wc_mod
    monkeypatch.setattr(
        wc_mod, "__file__",
        str(tmp_path / "vivarium-dashboard" / "vivarium_dashboard" / "lib" / "workspace_create.py"),
    )

    with pytest.raises(WorkspaceCreateError) as ei:
        find_pbg_template()
    assert ei.value.code == 500
    assert "pbg-template" in ei.value.message


def test_raises_when_all_fallbacks_missing(monkeypatch, tmp_path):
    """No env override, no sibling, and git clone fails (simulated by
    invalid repo URL)."""
    monkeypatch.delenv("PBG_TEMPLATE_PATH", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    from vivarium_dashboard.lib import workspace_create as wc_mod
    monkeypatch.setattr(wc_mod, "_PBG_TEMPLATE_REPO",
                        "https://github.com/nonexistent-org/pbg-template.git")
    # Isolate from the real sibling pbg-template checkout — same rationale
    # as test_cache_clone_raises_when_no_git above.
    monkeypatch.setattr(
        wc_mod, "__file__",
        str(tmp_path / "vivarium-dashboard" / "vivarium_dashboard" / "lib" / "workspace_create.py"),
    )

    with pytest.raises(WorkspaceCreateError) as ei:
        find_pbg_template()
    assert ei.value.code == 500
    assert "pbg-template" in ei.value.message

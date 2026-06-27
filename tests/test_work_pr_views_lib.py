"""Behavioural tests for vivarium_dashboard.lib.work_pr_views.

``work_create_pr`` is a byte-identical port of the stdlib handler
``_post_work_create_pr``.  Every test monkeypatches the lib seam reached via the
``work_pr_views`` module (``subprocess`` / ``shutil`` / ``work_state`` /
``report``) so NO test ever runs real git or gh.  The full status-path matrix is
covered, including the opportunistic pushed-mark, the default-PR-title lookup,
the ``investigation:`` prefix heuristic, and the gh command + save_state on the
happy path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vivarium_dashboard.lib import work_pr_views as wp


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _patch_state(monkeypatch, state: dict) -> list:
    """Patch load_state_or_adopt_current → ``state`` and record save_state calls."""
    saved: list = []
    monkeypatch.setattr(wp.work_state, "load_state_or_adopt_current", lambda: state)
    monkeypatch.setattr(wp.work_state, "save_state", lambda s: saved.append(dict(s)))
    return saved


# ===========================================================================
# Early refusals
# ===========================================================================
def test_no_active_workstream_409(monkeypatch):
    _patch_state(monkeypatch, {})
    resp, code = wp.work_create_pr(Path("/ws"), {})
    assert (resp, code) == ({"error": "no active workstream"}, 409)


def test_not_pushed_long_message_409(monkeypatch):
    """Branch with no upstream + rev-list says ahead → the long not-pushed 409."""
    _patch_state(monkeypatch, {"active_branch": "feat/x", "pushed": False})

    def _run(argv, *a, **k):
        # rev-list reports HEAD ahead of origin (right part != "0")
        assert argv[:3] == ["git", "rev-list", "--left-right"]
        return _cp(0, stdout="0\t3\n")

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_create_pr(Path("/ws"), {})
    assert code == 409
    assert resp["error"] == (
        "branch not yet pushed. Use the workstream strip at "
        "the top of the dashboard — click `Link branch to "
        "upstream` (if shown) to create the remote and push, "
        "or `Push` (if the branch already has an upstream). "
        "Terminal fallback: `git push -u origin <branch>`; "
        "the dashboard picks it up on the next refresh."
    )


def test_opportunistic_pushed_mark(monkeypatch):
    """rev-list right-count == 0 → state["pushed"]=True + save_state, then proceeds."""
    state = {"active_branch": "feat/x", "pushed": False, "pr_url": "u"}
    saved = _patch_state(monkeypatch, state)

    def _run(argv, *a, **k):
        assert argv[:3] == ["git", "rev-list", "--left-right"]
        return _cp(0, stdout="2\t0\n")  # right == "0" → local not ahead

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_create_pr(Path("/ws"), {})
    # pushed got marked + saved, so we fall through to the pr_url-exists branch
    assert state["pushed"] is True
    assert any(s.get("pushed") is True for s in saved)
    assert code == 409
    assert resp == {"error": "PR already exists: u", "pr_url": "u"}


def test_pr_already_exists_409(monkeypatch):
    _patch_state(monkeypatch, {"active_branch": "feat/x", "pushed": True, "pr_url": "http://x/pull/9"})
    resp, code = wp.work_create_pr(Path("/ws"), {})
    assert (resp, code) == (
        {"error": "PR already exists: http://x/pull/9", "pr_url": "http://x/pull/9"}, 409,
    )


# ===========================================================================
# _default_pr_title
# ===========================================================================
def test_default_pr_title_from_investigation_yaml(tmp_path):
    inv = tmp_path / "investigations" / "feat-x"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text("title: My Grand Investigation\n", encoding="utf-8")
    assert wp._default_pr_title(tmp_path, "feat-x") == "My Grand Investigation"


def test_default_pr_title_fallback(tmp_path):
    assert wp._default_pr_title(tmp_path, "feat-x") == "Workstream: feat-x"


# ===========================================================================
# gh not installed
# ===========================================================================
def test_gh_not_installed_500_with_manual_url(monkeypatch):
    _patch_state(monkeypatch, {"active_branch": "feat/x", "pushed": True, "base": "main"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: None)
    monkeypatch.setattr(wp.report, "_detect_github_repo", lambda ws: "owner/repo")
    resp, code = wp.work_create_pr(Path("/ws"), {"title": "T"})
    assert code == 500
    assert resp == {
        "error": "gh CLI not installed. Open manually:",
        "manual_url": "https://github.com/owner/repo/compare/main...feat/x?expand=1",
    }


def test_gh_not_installed_no_repo_manual_none(monkeypatch):
    _patch_state(monkeypatch, {"active_branch": "feat/x", "pushed": True})
    monkeypatch.setattr(wp.shutil, "which", lambda name: None)
    monkeypatch.setattr(wp.report, "_detect_github_repo", lambda ws: None)
    resp, code = wp.work_create_pr(Path("/ws"), {"title": "T"})
    assert (resp, code) == (
        {"error": "gh CLI not installed. Open manually:", "manual_url": None}, 500,
    )


# ===========================================================================
# investigation-prefix heuristic
# ===========================================================================
def test_investigation_prefix_heuristic(monkeypatch, tmp_path):
    """git diff touches investigations/ + title not prefixed → `investigation: ` prepended."""
    _patch_state(monkeypatch, {"active_branch": "feat/x", "pushed": True, "base": "main"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    captured = {}

    def _run(argv, *a, **k):
        if argv[:3] == ["git", "diff", "--name-only"]:
            return _cp(0, stdout="investigations/feat-x/study.yaml\nsrc/foo.py\n")
        if argv[:3] == ["gh", "pr", "create"]:
            captured["cmd"] = argv
            return _cp(0, stdout="https://github.com/o/r/pull/12\n")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_create_pr(tmp_path, {"title": "My title"})
    assert code == 200
    assert "--title" in captured["cmd"]
    ti = captured["cmd"].index("--title")
    assert captured["cmd"][ti + 1] == "investigation: My title"


def test_no_prefix_when_already_prefixed(monkeypatch, tmp_path):
    """Title already `investigation:`-prefixed → no git diff, no double prefix."""
    _patch_state(monkeypatch, {"active_branch": "feat/x", "pushed": True, "base": "main"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    captured = {}

    def _run(argv, *a, **k):
        if argv[:3] == ["git", "diff", "--name-only"]:
            raise AssertionError("git diff should be skipped when already prefixed")
        if argv[:3] == ["gh", "pr", "create"]:
            captured["cmd"] = argv
            return _cp(0, stdout="https://github.com/o/r/pull/3\n")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_create_pr(tmp_path, {"title": "investigation: keep"})
    ti = captured["cmd"].index("--title")
    assert captured["cmd"][ti + 1] == "investigation: keep"


# ===========================================================================
# gh pr create failure / happy path
# ===========================================================================
def test_gh_create_failure_500(monkeypatch, tmp_path):
    _patch_state(monkeypatch, {"active_branch": "feat/x", "pushed": True, "base": "main"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")

    def _run(argv, *a, **k):
        if argv[:3] == ["git", "diff", "--name-only"]:
            return _cp(0, stdout="src/foo.py\n")
        if argv[:3] == ["gh", "pr", "create"]:
            return _cp(1, stderr="boom: not authenticated")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_create_pr(tmp_path, {"title": "T"})
    assert (resp, code) == ({"error": "gh pr create failed: boom: not authenticated"}, 500)


def test_happy_200_with_cmd_and_save_state(monkeypatch, tmp_path):
    """Happy path: assert the gh cmd, draft default, save_state with pr_url/pr_number."""
    state = {"active_branch": "feat/x", "pushed": True, "base": "develop"}
    saved = _patch_state(monkeypatch, state)
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    captured = {}

    def _run(argv, *a, **k):
        if argv[:3] == ["git", "diff", "--name-only"]:
            return _cp(0, stdout="src/foo.py\n")
        if argv[:3] == ["gh", "pr", "create"]:
            captured["cmd"] = argv
            captured["cwd"] = k.get("cwd")
            return _cp(0, stdout="some noise\nhttps://github.com/o/r/pull/42\n")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_create_pr(tmp_path, {"title": "T", "body": "B"})
    assert code == 200
    assert resp == {"ok": True, "pr_url": "https://github.com/o/r/pull/42", "pr_number": 42}
    assert captured["cmd"] == [
        "gh", "pr", "create", "--base", "develop", "--head", "feat/x",
        "--title", "T", "--body", "B", "--draft",
    ]
    assert captured["cwd"] == tmp_path
    # save_state was called with the pr_url + pr_number recorded
    assert saved[-1]["pr_url"] == "https://github.com/o/r/pull/42"
    assert saved[-1]["pr_number"] == 42


def test_happy_200_draft_false_omits_flag(monkeypatch, tmp_path):
    _patch_state(monkeypatch, {"active_branch": "feat/x", "pushed": True, "base": "main"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    captured = {}

    def _run(argv, *a, **k):
        if argv[:3] == ["git", "diff", "--name-only"]:
            return _cp(0, stdout="src/foo.py\n")
        if argv[:3] == ["gh", "pr", "create"]:
            captured["cmd"] = argv
            return _cp(0, stdout="https://github.com/o/r/pull/7\n")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_create_pr(tmp_path, {"title": "T", "draft": False})
    assert code == 200
    assert "--draft" not in captured["cmd"]


def test_default_body_when_omitted(monkeypatch, tmp_path):
    _patch_state(monkeypatch, {"active_branch": "feat/x", "pushed": True, "base": "main"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    captured = {}

    def _run(argv, *a, **k):
        if argv[:3] == ["git", "diff", "--name-only"]:
            return _cp(0, stdout="src/foo.py\n")
        if argv[:3] == ["gh", "pr", "create"]:
            captured["cmd"] = argv
            return _cp(0, stdout="https://github.com/o/r/pull/1\n")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    wp.work_create_pr(tmp_path, {"title": "T"})
    bi = captured["cmd"].index("--body")
    assert captured["cmd"][bi + 1] == "Created via pbg-template dashboard."

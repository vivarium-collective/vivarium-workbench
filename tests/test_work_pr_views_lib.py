"""Behavioural tests for vivarium_workbench.lib.work_pr_views.

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

from vivarium_workbench.lib import work_pr_views as wp


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


# ===========================================================================
# default_upstream_repo
# ===========================================================================
def test_default_upstream_repo_from_workspace_yaml(tmp_path, monkeypatch):
    (tmp_path / "workspace.yaml").write_text(
        "upstream_repo: my-org/my-repo\n", encoding="utf-8")

    def _no_subprocess(*a, **k):  # workspace.yaml short-circuits before any git
        raise AssertionError("subprocess should not run when workspace.yaml set")

    monkeypatch.setattr(wp.subprocess, "run", _no_subprocess)
    assert wp.default_upstream_repo(tmp_path) == "my-org/my-repo"


def test_default_upstream_repo_from_external_remote(tmp_path, monkeypatch):
    # No workspace.yaml; external/v2ecoli exists with a git@ origin URL.
    (tmp_path / "external" / "v2ecoli").mkdir(parents=True)

    def _run(argv, *a, **k):
        assert argv == ["git", "remote", "get-url", "origin"]
        assert k.get("cwd") == tmp_path / "external" / "v2ecoli"
        return _cp(0, stdout="git@github.com:owner-x/name-y.git\n")

    monkeypatch.setattr(wp.subprocess, "run", _run)
    assert wp.default_upstream_repo(tmp_path) == "owner-x/name-y"


def test_default_upstream_repo_from_external_remote_https(tmp_path, monkeypatch):
    (tmp_path / "external" / "v2ecoli").mkdir(parents=True)
    monkeypatch.setattr(
        wp.subprocess, "run",
        lambda *a, **k: _cp(0, stdout="https://github.com/o2/n2.git\n"))
    assert wp.default_upstream_repo(tmp_path) == "o2/n2"


def test_default_upstream_repo_fallback(tmp_path, monkeypatch):
    # Neither workspace.yaml nor external/ dir → hard-coded fallback, no subprocess.
    monkeypatch.setattr(
        wp.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess")))
    assert wp.default_upstream_repo(tmp_path) == "vivarium-collective/v2ecoli"


def test_default_upstream_repo_empty_yaml_falls_through_to_fallback(tmp_path, monkeypatch):
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")  # no upstream_repo
    # external/ absent → fallback (subprocess never reached)
    monkeypatch.setattr(
        wp.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess")))
    assert wp.default_upstream_repo(tmp_path) == "vivarium-collective/v2ecoli"


# ===========================================================================
# work_link_branch — early refusals
# ===========================================================================
def test_link_no_active_workstream_409(monkeypatch):
    _patch_state(monkeypatch, {})
    resp, code = wp.work_link_branch(Path("/ws"), {})
    assert (resp, code) == (
        {"error": "no active workstream — Start one first so the push has a target"}, 409)


def test_link_gh_not_installed_500(monkeypatch):
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: None)
    resp, code = wp.work_link_branch(Path("/ws"), {})
    assert (resp, code) == (
        {"error": "gh CLI not installed. Install via `brew install gh` then `gh auth login`."}, 500)


def test_link_gh_not_authenticated_500(monkeypatch):
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(wp.subprocess, "run",
                        lambda argv, *a, **k: _cp(1, stderr="not logged in"))
    resp, code = wp.work_link_branch(Path("/ws"), {})
    assert (resp, code) == ({"error": "gh not authenticated. Run `gh auth login`."}, 500)


def test_link_bad_mode_400(monkeypatch):
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(wp.subprocess, "run", lambda argv, *a, **k: _cp(0))
    resp, code = wp.work_link_branch(Path("/ws"), {"mode": "Bogus"})
    assert (resp, code) == ({"error": "mode must be 'branch' or 'fork'; got 'bogus'"}, 400)


def test_link_bad_upstream_repo_400(monkeypatch):
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(wp.subprocess, "run", lambda argv, *a, **k: _cp(0))
    resp, code = wp.work_link_branch(Path("/ws"), {"upstream_repo": "not a repo!!"})
    assert code == 400
    assert resp == {"error": "upstream_repo must look like owner/name; got 'not a repo!!'"}


def test_link_bad_branch_name_400(monkeypatch):
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(wp.subprocess, "run", lambda argv, *a, **k: _cp(0))
    resp, code = wp.work_link_branch(
        Path("/ws"), {"upstream_repo": "o/r", "branch_name": "bad branch!"})
    assert (resp, code) == ({"error": "invalid branch name"}, 400)


def test_link_branch_rename_failure_500(monkeypatch):
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")

    def _run(argv, *a, **k):
        if argv[:2] == ["gh", "auth"]:
            return _cp(0)
        if argv[:3] == ["git", "branch", "-m"]:
            return _cp(1, stderr="rename boom")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_link_branch(
        Path("/ws"), {"upstream_repo": "o/r", "branch_name": "feat/y"})
    assert (resp, code) == ({"error": "branch rename failed: rename boom"}, 500)


# ===========================================================================
# work_link_branch — branch mode
# ===========================================================================
def test_link_branch_mode_happy_200(monkeypatch, tmp_path):
    """Origin absent → add origin + push; pushed marked + saved; 200 payload."""
    state = {"active_branch": "feat/x"}
    saved = _patch_state(monkeypatch, state)
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    calls = []

    def _run(argv, *a, **k):
        calls.append(argv)
        if argv[:2] == ["gh", "auth"]:
            return _cp(0)
        if argv == ["git", "remote", "get-url", "origin"]:
            return _cp(1)  # origin absent
        if argv[:3] == ["git", "remote", "add"]:
            assert argv == ["git", "remote", "add", "origin",
                            "https://github.com/o/r.git"]
            return _cp(0)
        if argv[:2] == ["git", "push"]:
            assert argv == ["git", "push", "-u", "origin", "feat/x"]
            return _cp(0)
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_link_branch(tmp_path, {"upstream_repo": "o/r"})
    assert code == 200
    assert resp == {
        "ok": True,
        "upstream_repo": "o/r",
        "branch": "feat/x",
        "branch_url": "https://github.com/o/r/tree/feat/x",
    }
    assert state["pushed"] is True
    assert saved[-1]["pushed"] is True


def test_link_branch_mode_no_push(monkeypatch, tmp_path):
    """push=False → no git push, but origin set + pushed marked + 200."""
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")

    def _run(argv, *a, **k):
        if argv[:2] == ["gh", "auth"]:
            return _cp(0)
        if argv == ["git", "remote", "get-url", "origin"]:
            return _cp(0, stdout="https://github.com/o/r.git\n")  # already correct
        if argv[:2] == ["git", "push"]:
            raise AssertionError("push must be skipped when push=False")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_link_branch(tmp_path, {"upstream_repo": "o/r", "push": False})
    assert code == 200
    assert resp["upstream_repo"] == "o/r"


def test_link_branch_origin_mismatch_409(monkeypatch, tmp_path):
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")

    def _run(argv, *a, **k):
        if argv[:2] == ["gh", "auth"]:
            return _cp(0)
        if argv == ["git", "remote", "get-url", "origin"]:
            return _cp(0, stdout="https://github.com/someone/else.git\n")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_link_branch(tmp_path, {"upstream_repo": "o/r"})
    assert code == 409
    assert resp == {
        "error": "origin already configured to https://github.com/someone/else.git; refusing to overwrite",
        "current_origin": "https://github.com/someone/else.git",
    }


def test_link_branch_push_failure_500(monkeypatch, tmp_path):
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")

    def _run(argv, *a, **k):
        if argv[:2] == ["gh", "auth"]:
            return _cp(0)
        if argv == ["git", "remote", "get-url", "origin"]:
            return _cp(1)
        if argv[:3] == ["git", "remote", "add"]:
            return _cp(0)
        if argv[:2] == ["git", "push"]:
            return _cp(1, stderr="push rejected")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_link_branch(tmp_path, {"upstream_repo": "o/r"})
    assert (resp, code) == ({"error": "git push failed: push rejected"}, 500)


# ===========================================================================
# work_link_branch — fork mode
# ===========================================================================
def test_link_fork_mode_happy_200(monkeypatch, tmp_path):
    state = {"active_branch": "feat/x"}
    saved = _patch_state(monkeypatch, state)
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")
    calls = []

    def _run(argv, *a, **k):
        calls.append(argv)
        if argv[:2] == ["gh", "auth"]:
            return _cp(0)
        if argv[:3] == ["gh", "repo", "fork"]:
            assert argv == ["gh", "repo", "fork", "o/r", "--remote=false", "--clone=false"]
            return _cp(0)
        if argv[:3] == ["gh", "api", "user"]:
            assert argv == ["gh", "api", "user", "--jq", ".login"]
            return _cp(0, stdout="me\n")
        if argv == ["git", "remote", "get-url", "origin"]:
            return _cp(1)  # absent → add
        if argv == ["git", "remote", "add", "origin", "https://github.com/me/r.git"]:
            return _cp(0)
        if argv == ["git", "remote", "get-url", "upstream"]:
            return _cp(1)  # absent → add
        if argv == ["git", "remote", "add", "upstream", "https://github.com/o/r.git"]:
            return _cp(0)
        if argv[:2] == ["git", "push"]:
            assert argv == ["git", "push", "-u", "origin", "feat/x"]
            return _cp(0)
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_link_branch(tmp_path, {"upstream_repo": "o/r", "mode": "fork"})
    assert code == 200
    assert resp == {
        "ok": True,
        "fork": "me/r",
        "upstream": "o/r",
        "branch": "feat/x",
        "branch_url": "https://github.com/me/r/tree/feat/x",
    }
    assert state["pushed"] is True
    assert saved[-1]["pushed"] is True


def test_link_fork_gh_fork_failure_500(monkeypatch, tmp_path):
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")

    def _run(argv, *a, **k):
        if argv[:2] == ["gh", "auth"]:
            return _cp(0)
        if argv[:3] == ["gh", "repo", "fork"]:
            return _cp(1, stderr="fork denied")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_link_branch(tmp_path, {"upstream_repo": "o/r", "mode": "fork"})
    assert (resp, code) == ({"error": "gh repo fork failed: fork denied"}, 500)


def test_link_fork_login_resolve_failure_500(monkeypatch, tmp_path):
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")

    def _run(argv, *a, **k):
        if argv[:2] == ["gh", "auth"]:
            return _cp(0)
        if argv[:3] == ["gh", "repo", "fork"]:
            return _cp(0)
        if argv[:3] == ["gh", "api", "user"]:
            return _cp(1, stderr="api boom")
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_link_branch(tmp_path, {"upstream_repo": "o/r", "mode": "fork"})
    assert (resp, code) == ({"error": "could not resolve gh login: api boom"}, 500)


def test_link_default_upstream_used_when_omitted(monkeypatch, tmp_path):
    """No upstream_repo in body → default_upstream_repo(ws_root) fallback feeds the URL."""
    _patch_state(monkeypatch, {"active_branch": "feat/x"})
    monkeypatch.setattr(wp.shutil, "which", lambda name: "/usr/bin/gh")

    def _run(argv, *a, **k):
        if argv[:2] == ["gh", "auth"]:
            return _cp(0)
        if argv == ["git", "remote", "get-url", "origin"]:
            return _cp(1)
        if argv[:3] == ["git", "remote", "add"]:
            assert argv == ["git", "remote", "add", "origin",
                            "https://github.com/vivarium-collective/v2ecoli.git"]
            return _cp(0)
        if argv[:2] == ["git", "push"]:
            return _cp(0)
        raise AssertionError(argv)

    monkeypatch.setattr(wp.subprocess, "run", _run)
    resp, code = wp.work_link_branch(tmp_path, {})  # tmp_path has no workspace.yaml/external
    assert code == 200
    assert resp["upstream_repo"] == "vivarium-collective/v2ecoli"

"""Behavioural tests for vivarium_workbench.lib.work_mutations.

The four builders are byte-identical ports of the stdlib workstream handlers
(``_post_work_start`` / ``_post_work_push`` / ``_post_work_end`` /
``_post_work_attach_report``).  Every test monkeypatches the lib seam reached
via the ``work_mutations`` module (``subprocess`` / ``work_state`` /
``git_status``) so NO test ever runs real git.  Each builder's full status-path
matrix is covered.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vivarium_workbench.lib import work_mutations as wm


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


# ===========================================================================
# work_start
# ===========================================================================
class TestWorkStart:
    def test_invalid_branch_400(self, monkeypatch):
        for body in ({}, {"branch": ""}, {"branch": "bad branch!"}, {"branch": "x" * 101}):
            resp, code = wm.work_start(Path("/ws"), body)
            assert (resp, code) == ({"error": "invalid branch name"}, 400)

    def test_already_on_workstream_409(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {"active_branch": "feat/old"})
        resp, code = wm.work_start(Path("/ws"), {"branch": "feat/new"})
        assert code == 409
        assert resp == {"error": "already on workstream 'feat/old'. End it first."}

    def test_dirty_tree_409(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {})
        monkeypatch.setattr(wm.git_status, "dirty_workspace", lambda ws: " M file.py")
        resp, code = wm.work_start(Path("/ws"), {"branch": "feat/new"})
        assert (resp, code) == ({"error": "working tree dirty — commit or stash first"}, 409)

    def test_base_not_found_404(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {})
        monkeypatch.setattr(wm.git_status, "dirty_workspace", lambda ws: "")

        def _run(argv, *a, **k):
            assert argv == ["git", "rev-parse", "--verify", "nope"]
            return _cp(1)

        monkeypatch.setattr(wm.subprocess, "run", _run)
        resp, code = wm.work_start(Path("/ws"), {"branch": "feat/new", "base": "nope"})
        assert (resp, code) == ({"error": "base branch 'nope' not found"}, 404)

    def test_branch_exists_409(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {})
        monkeypatch.setattr(wm.git_status, "dirty_workspace", lambda ws: "")

        def _run(argv, *a, **k):
            # base verify ok, branch verify ok (returncode 0 == exists)
            return _cp(0)

        monkeypatch.setattr(wm.subprocess, "run", _run)
        resp, code = wm.work_start(Path("/ws"), {"branch": "feat/new"})
        assert code == 409
        assert resp == {"error": "branch 'feat/new' already exists. Pick a different name or delete the old one."}

    def test_create_fail_500(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {})
        monkeypatch.setattr(wm.git_status, "dirty_workspace", lambda ws: "")

        def _run(argv, *a, **k):
            if argv[:3] == ["git", "rev-parse", "--verify"]:
                # base exists (0); branch does NOT exist (1)
                return _cp(0) if argv[3] == "main" else _cp(1)
            if argv[:2] == ["git", "checkout"] and argv[2] == "main":
                return _cp(0)
            if argv[:3] == ["git", "checkout", "-b"]:
                return _cp(128, stderr="fatal: boom" + "x" * 400)
            return _cp(0)

        monkeypatch.setattr(wm.subprocess, "run", _run)
        resp, code = wm.work_start(Path("/ws"), {"branch": "feat/new"})
        assert code == 500
        assert resp["error"].startswith("branch create failed: fatal: boom")
        assert len(resp["error"]) == len("branch create failed: ") + 300  # stderr[:300]

    def test_happy_200(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {})
        monkeypatch.setattr(wm.git_status, "dirty_workspace", lambda ws: "")
        calls = []
        saved = {}

        def _run(argv, *a, **k):
            calls.append(argv)
            if argv[:3] == ["git", "rev-parse", "--verify"]:
                return _cp(0) if argv[3] == "develop" else _cp(1)
            return _cp(0)

        monkeypatch.setattr(wm.subprocess, "run", _run)
        monkeypatch.setattr(wm.work_state, "save_state", lambda s: saved.update(s))
        resp, code = wm.work_start(Path("/ws"), {"branch": "feat/new", "base": "develop"})
        assert (resp, code) == ({"ok": True, "branch": "feat/new", "base": "develop"}, 200)
        assert saved == {"active_branch": "feat/new", "base": "develop",
                         "pushed": False, "pr_number": None, "pr_url": None}
        # checkout base then checkout -b branch, in order
        checkouts = [c for c in calls if c[:2] == ["git", "checkout"]]
        assert checkouts == [["git", "checkout", "develop"], ["git", "checkout", "-b", "feat/new"]]


# ===========================================================================
# work_push
# ===========================================================================
class TestWorkPush:
    def test_no_workstream_409(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state_or_adopt_current", lambda: {})
        resp, code = wm.work_push(Path("/ws"), {})
        assert (resp, code) == ({"error": "no active workstream"}, 409)

    def test_no_origin_409_exact_body(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state_or_adopt_current",
                            lambda: {"active_branch": "feat/x"})
        monkeypatch.setattr(wm.git_status, "has_origin_remote", lambda ws: False)
        resp, code = wm.work_push(Path("/ws"), {})
        assert code == 409
        assert resp == {
            "error": "no GitHub remote configured",
            "diagnosis": {
                "category": "no_origin",
                "summary": "This workspace has no `origin` remote yet.",
                "suggestion": "Click `Create GitHub repo` in the workstream strip to create one in your account and push in a single step.",
            },
        }

    def test_push_fail_500_with_diagnosis(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state_or_adopt_current",
                            lambda: {"active_branch": "feat/x"})
        monkeypatch.setattr(wm.git_status, "has_origin_remote", lambda ws: True)
        monkeypatch.setattr(wm.subprocess, "run",
                            lambda *a, **k: _cp(1, stderr="Permission to o/r denied to user"))
        resp, code = wm.work_push(Path("/ws"), {})
        assert code == 500
        assert resp["error"] == "push failed: Permission to o/r denied to user"
        assert resp["diagnosis"]["category"] == "auth"

    def test_push_fail_500_no_diagnosis(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state_or_adopt_current",
                            lambda: {"active_branch": "feat/x"})
        monkeypatch.setattr(wm.git_status, "has_origin_remote", lambda ws: True)
        monkeypatch.setattr(wm.subprocess, "run",
                            lambda *a, **k: _cp(1, stdout="weird unknown failure"))
        resp, code = wm.work_push(Path("/ws"), {})
        assert code == 500
        assert resp == {"error": "push failed: weird unknown failure"}
        assert "diagnosis" not in resp

    def test_happy_200(self, monkeypatch):
        state = {"active_branch": "feat/x"}
        monkeypatch.setattr(wm.work_state, "load_state_or_adopt_current", lambda: state)
        monkeypatch.setattr(wm.git_status, "has_origin_remote", lambda ws: True)
        saved = {}
        monkeypatch.setattr(wm.work_state, "save_state", lambda s: saved.update(s))
        long_log = "line\n" * 100  # > 300 chars
        monkeypatch.setattr(wm.subprocess, "run", lambda *a, **k: _cp(0, stdout=long_log))
        resp, code = wm.work_push(Path("/ws"), {})
        assert code == 200
        assert resp == {"ok": True, "branch": "feat/x", "log": long_log[-300:]}
        assert saved["pushed"] is True


# ===========================================================================
# work_end
# ===========================================================================
class TestWorkEnd:
    def test_no_workstream_409(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {})
        resp, code = wm.work_end(Path("/ws"), {})
        assert (resp, code) == ({"error": "no active workstream"}, 409)

    def test_dirty_409(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {"active_branch": "feat/x"})
        monkeypatch.setattr(wm.git_status, "dirty_workspace", lambda ws: " M f.py")
        resp, code = wm.work_end(Path("/ws"), {})
        assert (resp, code) == ({"error": "uncommitted changes — commit or stash before ending"}, 409)

    def test_happy_200(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state",
                            lambda: {"active_branch": "feat/x", "base": "develop"})
        monkeypatch.setattr(wm.git_status, "dirty_workspace", lambda ws: "")
        calls = []
        cleared = {"n": 0}
        monkeypatch.setattr(wm.subprocess, "run",
                            lambda argv, *a, **k: calls.append(argv) or _cp(0))
        monkeypatch.setattr(wm.work_state, "clear_state",
                            lambda: cleared.__setitem__("n", cleared["n"] + 1))
        resp, code = wm.work_end(Path("/ws"), {})
        assert (resp, code) == ({"ok": True}, 200)
        assert calls == [["git", "checkout", "develop"]]
        assert cleared["n"] == 1

    def test_base_defaults_to_main(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {"active_branch": "feat/x"})
        monkeypatch.setattr(wm.git_status, "dirty_workspace", lambda ws: "")
        calls = []
        monkeypatch.setattr(wm.subprocess, "run",
                            lambda argv, *a, **k: calls.append(argv) or _cp(0))
        monkeypatch.setattr(wm.work_state, "clear_state", lambda: None)
        wm.work_end(Path("/ws"), {})
        assert calls == [["git", "checkout", "main"]]


# ===========================================================================
# work_attach_report
# ===========================================================================
class TestWorkAttachReport:
    def test_no_branch_409(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {})
        resp, code = wm.work_attach_report(Path("/ws"), {"filename": "r.html", "html": "<x>"})
        assert (resp, code) == ({"error": "no active investigation branch"}, 409)

    def test_missing_filename_or_html_400(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {"active_branch": "feat/x"})
        for body in ({"html": "<x>"}, {"filename": "r.html"},
                     {"filename": "r.html", "html": ""},
                     {"filename": "r.html", "html": 123}):
            resp, code = wm.work_attach_report(Path("/ws"), body)
            assert (resp, code) == ({"error": "filename + html required"}, 400)

    def test_pathy_filename_400(self, monkeypatch):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {"active_branch": "feat/x"})
        for fn in ("sub/r.html", ".hidden"):
            resp, code = wm.work_attach_report(Path("/ws"), {"filename": fn, "html": "<x>"})
            assert (resp, code) == (
                {"error": "filename must be a bare name (no path / no leading .)"}, 400)

    def test_git_add_fail_500(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {"active_branch": "feat/x"})
        monkeypatch.setattr(wm.subprocess, "run",
                            lambda *a, **k: _cp(1, stderr="add boom"))
        resp, code = wm.work_attach_report(tmp_path, {"filename": "r.html", "html": "<x>"})
        assert (resp, code) == ({"error": "git add failed: add boom"}, 500)

    def test_nothing_to_commit_soft_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {"active_branch": "feat/x"})

        def _run(argv, *a, **k):
            if argv[:2] == ["git", "add"]:
                return _cp(0)
            if argv[:2] == ["git", "commit"]:
                return _cp(1, stdout="nothing to commit, working tree clean")
            return _cp(0)

        monkeypatch.setattr(wm.subprocess, "run", _run)
        resp, code = wm.work_attach_report(tmp_path, {"filename": "r.html", "html": "<x>"})
        assert code == 200
        assert resp == {"ok": True, "unchanged": True, "path": "reports/r.html", "branch": "feat/x"}

    def test_commit_fail_500(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {"active_branch": "feat/x"})

        def _run(argv, *a, **k):
            if argv[:2] == ["git", "add"]:
                return _cp(0)
            if argv[:2] == ["git", "commit"]:
                return _cp(1, stderr="commit boom")
            return _cp(0)

        monkeypatch.setattr(wm.subprocess, "run", _run)
        resp, code = wm.work_attach_report(tmp_path, {"filename": "r.html", "html": "<x>"})
        assert (resp, code) == ({"error": "git commit failed: commit boom"}, 500)

    def test_happy_200_real_file_write(self, monkeypatch, tmp_path):
        """Use a real tmp ws_root so the report file write is exercised + asserted."""
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {"active_branch": "feat/x"})

        def _run(argv, *a, **k):
            if argv[:2] == ["git", "add"]:
                return _cp(0)
            if argv[:2] == ["git", "commit"]:
                return _cp(0)
            if argv[:3] == ["git", "rev-parse", "HEAD"]:
                return _cp(0, stdout="deadbeefcafe\n")
            return _cp(0)

        monkeypatch.setattr(wm.subprocess, "run", _run)
        resp, code = wm.work_attach_report(
            tmp_path, {"filename": "report.html", "html": "<html>hi</html>"})
        assert code == 200
        assert resp == {"ok": True, "path": "reports/report.html",
                        "branch": "feat/x", "commit_sha": "deadbeefcafe"}
        # the file was really written under reports/
        written = tmp_path / "reports" / "report.html"
        assert written.read_text() == "<html>hi</html>"

    def test_default_commit_message(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wm.work_state, "load_state", lambda: {"active_branch": "feat/x"})
        seen = {}

        def _run(argv, *a, **k):
            if argv[:2] == ["git", "commit"]:
                seen["msg"] = argv[argv.index("-m") + 1]
                return _cp(0)
            if argv[:3] == ["git", "rev-parse", "HEAD"]:
                return _cp(0, stdout="sha")
            return _cp(0)

        monkeypatch.setattr(wm.subprocess, "run", _run)
        wm.work_attach_report(tmp_path, {"filename": "r.html", "html": "<x>"})
        assert seen["msg"] == "docs(report): attach r.html"

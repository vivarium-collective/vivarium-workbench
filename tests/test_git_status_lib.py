"""Parity and unit tests for vivarium_dashboard.lib.git_status.

Every test builds a hermetic git repo in ``tmp_path`` (no touches to the real
repo).  The primary assertion is that the lib builder returns the expected dict
shape; secondary assertions compare lib-builder output to logic parity.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vivarium_dashboard.lib import git_status as gs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _git(ws: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in *ws*."""
    return subprocess.run(
        ["git", "-C", str(ws), *args],
        capture_output=True, text=True, check=check,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Tiny hermetic git repo: one commit on main."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("hello\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


@pytest.fixture()
def repo_with_branch(repo: Path) -> Path:
    """Hermetic repo with a second commit on a feature branch."""
    _git(repo, "checkout", "-b", "feature/x")
    (repo / "new_file.py").write_text("# new\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add new file")
    _git(repo, "checkout", "main")
    return repo


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_has_origin_remote_false(self, repo: Path) -> None:
        assert gs.has_origin_remote(repo) is False

    def test_stale_branch_threshold_default(self) -> None:
        assert gs.stale_branch_threshold() == 20

    def test_stale_branch_threshold_env(self, monkeypatch) -> None:
        monkeypatch.setenv("PBG_STALE_BRANCH_THRESHOLD", "5")
        assert gs.stale_branch_threshold() == 5

    def test_stale_branch_threshold_env_invalid(self, monkeypatch) -> None:
        monkeypatch.setenv("PBG_STALE_BRANCH_THRESHOLD", "notanint")
        assert gs.stale_branch_threshold() == 20

    def test_commits_behind_zero(self, repo: Path) -> None:
        # No origin, local base only
        cb, ref = gs.commits_behind(repo, "main", "main")
        assert cb == 0  # same ref → 0

    def test_commits_behind_feature_vs_main(self, repo_with_branch: Path) -> None:
        # feature/x is ahead of main by 1 commit, so main is 0 behind feature/x
        # But feature/x is 0 behind main (it branched from main HEAD)
        cb, ref = gs.commits_behind(repo_with_branch, "feature/x", "main")
        assert cb == 0  # feature/x already contains all of main

    def test_dirty_workspace_clean(self, repo: Path) -> None:
        result = gs.dirty_workspace(repo)
        assert result.strip() == ""

    def test_dirty_workspace_with_untracked(self, repo: Path) -> None:
        (repo / "untracked.txt").write_text("dirty\n")
        result = gs.dirty_workspace(repo)
        assert "untracked.txt" in result

    def test_dirty_workspace_excludes_reports(self, repo: Path) -> None:
        (repo / "reports").mkdir()
        (repo / "reports" / "foo.html").write_text("report\n")
        result = gs.dirty_workspace(repo)
        assert "reports/" not in result

    def test_dirty_workspace_excludes_out(self, repo: Path) -> None:
        (repo / "out").mkdir()
        (repo / "out" / "cache.json").write_text("{}\n")
        result = gs.dirty_workspace(repo)
        assert "out/" not in result

    def test_dirty_workspace_excludes_pbg(self, repo: Path) -> None:
        (repo / ".pbg").mkdir()
        (repo / ".pbg" / "state.json").write_text("{}\n")
        result = gs.dirty_workspace(repo)
        assert ".pbg/" not in result

    def test_submodule_paths_no_gitmodules(self, repo: Path) -> None:
        assert gs.submodule_paths(repo) == set()

    def test_is_generated_path(self) -> None:
        assert gs.is_generated_path("reports/foo.html")
        assert gs.is_generated_path("out/cache.json")
        assert gs.is_generated_path(".pbg/state.json")
        assert not gs.is_generated_path("studies/dnaa/spec.yaml")


# ---------------------------------------------------------------------------
# remote_repo_url / remote_push_and_sha (C-state-3c extractions)
#
# subprocess is fully monkeypatched — these never shell out to a real git or
# touch the network, only the lib's branching logic is exercised.
# ---------------------------------------------------------------------------

def _cp(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Build a fake subprocess.CompletedProcess-like object."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestRemoteRepoUrl:
    def test_non_zero_returns_none(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: _cp(returncode=128, stdout=""))
        assert gs.remote_repo_url(tmp_path) is None

    def test_empty_url_returns_none(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: _cp(returncode=0, stdout="   \n"))
        assert gs.remote_repo_url(tmp_path) is None

    def test_success_normalizes_url(self, monkeypatch, tmp_path: Path) -> None:
        # The .git suffix is stripped by the reused lib _normalize_repo_url.
        monkeypatch.setattr(
            gs.subprocess, "run",
            lambda *a, **k: _cp(returncode=0, stdout="https://github.com/x/y.git\n"),
        )
        assert gs.remote_repo_url(tmp_path) == "https://github.com/x/y"

    def test_uses_lib_normalize_not_a_new_copy(self, monkeypatch, tmp_path: Path) -> None:
        """remote_repo_url routes through lib.source_build_views._normalize_repo_url."""
        from vivarium_dashboard.lib import source_build_views as sbv
        monkeypatch.setattr(
            gs.subprocess, "run",
            lambda *a, **k: _cp(returncode=0, stdout="ssh://git@host/r.git"),
        )
        monkeypatch.setattr(sbv, "_normalize_repo_url", lambda u: "SENTINEL")
        assert gs.remote_repo_url(tmp_path) == "SENTINEL"


class TestRemotePushAndSha:
    def test_success_returns_sha(self, monkeypatch, tmp_path: Path) -> None:
        from vivarium_dashboard.lib import github_auth
        monkeypatch.setattr(github_auth, "current_token_env", lambda: {})
        calls = []

        def _fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["git", "rev-parse"] and "--abbrev-ref" in args:
                return _cp(stdout="feature/x\n")
            if args[:2] == ["git", "push"]:
                return _cp(returncode=0)
            if args[:2] == ["git", "rev-parse"]:  # HEAD sha
                return _cp(stdout="deadbeef\n")
            raise AssertionError(f"unexpected git call: {args}")

        monkeypatch.setattr(gs.subprocess, "run", _fake_run)
        assert gs.remote_push_and_sha(tmp_path) == "deadbeef"
        # Pushed -u origin <branch> with the resolved branch.
        assert ["git", "push", "-u", "origin", "feature/x"] in calls

    def test_detached_head_raises(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: _cp(stdout="HEAD\n"))
        with pytest.raises(RuntimeError, match="not on a named branch"):
            gs.remote_push_and_sha(tmp_path)

    def test_empty_branch_raises(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: _cp(stdout="\n"))
        with pytest.raises(RuntimeError, match="not on a named branch"):
            gs.remote_push_and_sha(tmp_path)

    def test_push_failure_raises_with_tail(self, monkeypatch, tmp_path: Path) -> None:
        from vivarium_dashboard.lib import github_auth
        monkeypatch.setattr(github_auth, "current_token_env", lambda: {})

        def _fake_run(args, **kwargs):
            if "--abbrev-ref" in args:
                return _cp(stdout="feature/x\n")
            if args[:2] == ["git", "push"]:
                return _cp(returncode=1, stderr="remote: Permission denied\n")
            raise AssertionError(f"unexpected git call: {args}")

        monkeypatch.setattr(gs.subprocess, "run", _fake_run)
        with pytest.raises(RuntimeError, match="git push failed:.*Permission denied"):
            gs.remote_push_and_sha(tmp_path)

    def test_empty_sha_raises(self, monkeypatch, tmp_path: Path) -> None:
        from vivarium_dashboard.lib import github_auth
        monkeypatch.setattr(github_auth, "current_token_env", lambda: {})

        def _fake_run(args, **kwargs):
            if "--abbrev-ref" in args:
                return _cp(stdout="feature/x\n")
            if args[:2] == ["git", "push"]:
                return _cp(returncode=0)
            if args[:2] == ["git", "rev-parse"]:
                return _cp(stdout="\n")  # empty HEAD sha
            raise AssertionError(f"unexpected git call: {args}")

        monkeypatch.setattr(gs.subprocess, "run", _fake_run)
        with pytest.raises(RuntimeError, match="could not resolve HEAD commit"):
            gs.remote_push_and_sha(tmp_path)


# ---------------------------------------------------------------------------
# build_git_status
# ---------------------------------------------------------------------------

class TestBuildGitStatus:
    def test_not_a_git_repo(self, tmp_path: Path) -> None:
        """Non-git dir → returns the default result dict (no crash)."""
        result = gs.build_git_status(tmp_path)
        assert isinstance(result, dict)
        assert result["branch"] is None
        assert result["push_state"] == "no_origin"

    def test_git_repo_no_origin(self, repo: Path) -> None:
        result = gs.build_git_status(repo)
        assert result["branch"] == "main"
        assert result["push_state"] == "no_origin"  # no origin configured
        assert result["upstream_repo"] is None
        assert result["gh_available"] in (True, False)  # bool, not None
        assert result["has_active_workstream"] is False

    def test_includes_all_expected_keys(self, repo: Path) -> None:
        result = gs.build_git_status(repo)
        expected_keys = {
            "upstream_repo", "branch", "push_state", "ahead", "behind",
            "branch_url", "repo_url", "pr_number", "pr_url", "base",
            "ahead_of_base", "dirty_count", "compare_url", "pr_state",
            "gh_available", "has_active_workstream",
        }
        assert expected_keys.issubset(result.keys())

    def test_dirty_count_zero_no_origin(self, repo: Path) -> None:
        """Without an origin remote, build_git_status returns early before
        computing dirty_count (matches original _get_git_status behaviour)."""
        (repo / "dirty.txt").write_text("change\n")
        result = gs.build_git_status(repo)
        # Returns early after origin check fails → dirty_count stays at default 0
        assert result["dirty_count"] == 0


# ---------------------------------------------------------------------------
# build_work_status
# ---------------------------------------------------------------------------

class TestBuildWorkStatus:
    def test_no_state_file(self, repo: Path) -> None:
        result = gs.build_work_status(repo)
        assert result == {"active": False}

    def test_with_active_state(self, repo_with_branch: Path) -> None:
        pbg_dir = repo_with_branch / ".pbg"
        pbg_dir.mkdir()
        state = {
            "active_branch": "feature/x",
            "base": "main",
            "pushed": False,
        }
        (pbg_dir / "state.json").write_text(json.dumps(state))

        result = gs.build_work_status(repo_with_branch)
        assert result["active"] is True
        assert result["branch"] == "feature/x"
        assert result["base"] == "main"
        assert isinstance(result["commits_ahead"], int)
        assert isinstance(result["commits_behind"], int)
        assert isinstance(result["stale"], bool)
        assert "pr_number" in result

    def test_inactive_missing_keys(self, repo: Path) -> None:
        result = gs.build_work_status(repo)
        assert result == {"active": False}
        assert "branch" not in result


# ---------------------------------------------------------------------------
# build_branch_staleness
# ---------------------------------------------------------------------------

class TestBuildBranchStaleness:
    def test_with_branch_param(self, repo: Path) -> None:
        result = gs.build_branch_staleness(repo, branch="main", base="main")
        assert result["branch"] == "main"
        assert isinstance(result["commits_behind"], int)
        assert isinstance(result["stale"], bool)
        assert "stale_threshold" in result

    def test_auto_detect_branch(self, repo: Path) -> None:
        """When branch=None, auto-detects HEAD (should be 'main')."""
        result = gs.build_branch_staleness(repo, branch=None, base="main")
        assert result["branch"] == "main"

    def test_raises_no_branch_error_in_non_git(self, tmp_path: Path) -> None:
        """Non-git dir + no branch param → NoBranchError (→ HTTP 400)."""
        with pytest.raises(gs.NoBranchError):
            gs.build_branch_staleness(tmp_path, branch=None)

    def test_feature_branch_staleness(self, repo_with_branch: Path) -> None:
        result = gs.build_branch_staleness(repo_with_branch, branch="feature/x", base="main")
        assert result["branch"] == "feature/x"
        # feature/x has all of main's commits, so commits_behind==0
        assert result["commits_behind"] == 0
        assert result["stale"] is False


# ---------------------------------------------------------------------------
# build_dirty_status
# ---------------------------------------------------------------------------

class TestBuildDirtyStatus:
    def test_clean_repo(self, repo: Path) -> None:
        result = gs.build_dirty_status(repo)
        assert result["count"] == 0
        assert result["files"] == []

    def test_with_modified_file(self, repo: Path) -> None:
        (repo / "README.md").write_text("modified\n")
        result = gs.build_dirty_status(repo)
        assert result["count"] >= 1
        paths = [f["path"] for f in result["files"]]
        assert "README.md" in paths

    def test_files_have_status_and_path(self, repo: Path) -> None:
        (repo / "new.txt").write_text("new\n")
        result = gs.build_dirty_status(repo)
        for f in result["files"]:
            assert "status" in f
            assert "path" in f

    def test_raises_on_non_git_dir(self, tmp_path: Path) -> None:
        """git status --check=True fails in a non-git dir → CalledProcessError."""
        import subprocess
        with pytest.raises(subprocess.CalledProcessError):
            gs.build_dirty_status(tmp_path)


# ---------------------------------------------------------------------------
# list_branches
# ---------------------------------------------------------------------------

class TestListBranches:
    def test_no_stage_branches(self, repo: Path) -> None:
        result = gs.list_branches(repo)
        assert result["branches"] == []
        assert result["current"] == "main"

    def test_with_stage_branch(self, repo: Path) -> None:
        _git(repo, "checkout", "-b", "stage/feature-a")
        (repo / "feature_a.py").write_text("# a\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "feature a")
        _git(repo, "checkout", "main")

        result = gs.list_branches(repo)
        names = [b["name"] for b in result["branches"]]
        assert "stage/feature-a" in names
        b = next(b for b in result["branches"] if b["name"] == "stage/feature-a")
        assert "sha" in b["last_commit"]
        assert "subject" in b["last_commit"]
        assert isinstance(b["ahead_of_main"], int)

    def test_current_field(self, repo: Path) -> None:
        result = gs.list_branches(repo)
        assert result["current"] == "main"

    def test_non_git_dir_returns_error(self, tmp_path: Path) -> None:
        result = gs.list_branches(tmp_path)
        assert "error" in result


# ---------------------------------------------------------------------------
# build_branch_diff
# ---------------------------------------------------------------------------

class TestBuildBranchDiff:
    def test_valid_branch(self, repo_with_branch: Path) -> None:
        result = gs.build_branch_diff(repo_with_branch, "feature/x")
        assert result["branch"] == "feature/x"
        assert isinstance(result["log"], str)
        assert isinstance(result["diff_stat"], str)

    def test_branch_log_shows_commit(self, repo_with_branch: Path) -> None:
        result = gs.build_branch_diff(repo_with_branch, "feature/x")
        # feature/x has one commit not on main
        assert "add new file" in result["log"]

    def test_invalid_branch_name_raises(self, repo: Path) -> None:
        with pytest.raises(ValueError):
            gs.build_branch_diff(repo, "../evil")

    def test_empty_branch_raises(self, repo: Path) -> None:
        with pytest.raises(ValueError):
            gs.build_branch_diff(repo, "")

    def test_dotdot_in_branch_raises(self, repo: Path) -> None:
        with pytest.raises(ValueError):
            gs.build_branch_diff(repo, "feat..evil")


# ---------------------------------------------------------------------------
# Cross-shim parity: legacy server.py handler body == lib-builder body
# ---------------------------------------------------------------------------

class TestServerShimParity:
    """Spec-required parity test: the legacy stdlib handler must produce the
    SAME JSON body (and status code) as the lib builder on the same real-git
    fixture.

    The handlers now delegate to ``lib.git_status``, so these tests exercise
    the real wiring — query-string parsing, the WORKSPACE plumbing, and the
    status-code mapping — by invoking the actual ``server.Handler`` methods
    (constructed via ``__new__`` so we bypass the socket-bound ``__init__``
    and capture the ``self._json(body, status)`` call).  The equality assertion
    is real, not a tautology: it confirms the handler hands back exactly what
    the builder produces, including the 200/400/500 codes.
    """

    @staticmethod
    def _invoke(monkeypatch, ws_root: Path, method_name: str, path: str = "/") -> dict:
        import vivarium_dashboard.server as server

        monkeypatch.setattr(server, "WORKSPACE", ws_root)
        handler = server.Handler.__new__(server.Handler)
        captured: dict = {}

        def _fake_json(data, code):
            captured["body"] = data
            captured["status"] = code

        handler._json = _fake_json          # type: ignore[method-assign]
        handler.path = path
        getattr(handler, method_name)()
        return captured

    def test_git_status_parity(self, monkeypatch, repo: Path) -> None:
        # Add an origin remote so the upstream-parsing branch is exercised.
        _git(repo, "remote", "add", "origin", "https://github.com/acme/widgets.git")
        captured = self._invoke(monkeypatch, repo, "_get_git_status", "/api/git-status")
        assert captured["status"] == 200
        assert captured["body"] == gs.build_git_status(repo)
        assert captured["body"]["upstream_repo"] == "acme/widgets"   # non-trivial

    def test_work_status_active_parity(self, monkeypatch, repo_with_branch: Path) -> None:
        pbg = repo_with_branch / ".pbg"
        pbg.mkdir()
        (pbg / "state.json").write_text(
            json.dumps({"active_branch": "feature/x", "base": "main", "pushed": False})
        )
        captured = self._invoke(
            monkeypatch, repo_with_branch, "_get_work_status", "/api/work-status"
        )
        assert captured["status"] == 200
        assert captured["body"] == gs.build_work_status(repo_with_branch)
        assert captured["body"]["active"] is True   # real active payload, not {active:false}

    def test_work_status_inactive_parity(self, monkeypatch, repo: Path) -> None:
        captured = self._invoke(monkeypatch, repo, "_get_work_status", "/api/work-status")
        assert captured["status"] == 200
        assert captured["body"] == gs.build_work_status(repo) == {"active": False}

    def test_branches_parity(self, monkeypatch, repo: Path) -> None:
        _git(repo, "checkout", "-b", "stage/x")
        (repo / "s.py").write_text("# s\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "stage work")
        _git(repo, "checkout", "main")
        captured = self._invoke(monkeypatch, repo, "_serve_branches", "/api/branches")
        assert captured["status"] == 200
        assert captured["body"] == gs.list_branches(repo)
        assert [b["name"] for b in captured["body"]["branches"]] == ["stage/x"]

    def test_branch_staleness_parity(self, monkeypatch, repo_with_branch: Path) -> None:
        captured = self._invoke(
            monkeypatch, repo_with_branch, "_get_branch_staleness",
            "/api/branch-staleness?branch=feature/x",
        )
        assert captured["status"] == 200
        assert captured["body"] == gs.build_branch_staleness(
            repo_with_branch, "feature/x", "main"
        )

    def test_branch_staleness_400_parity(self, monkeypatch, tmp_path: Path) -> None:
        """Non-git dir + no ?branch= → handler maps NoBranchError to 400."""
        captured = self._invoke(
            monkeypatch, tmp_path, "_get_branch_staleness", "/api/branch-staleness"
        )
        assert captured["status"] == 400
        assert "could not determine current branch" in captured["body"]["error"]

    def test_branch_diff_parity(self, monkeypatch, repo_with_branch: Path) -> None:
        captured = self._invoke(
            monkeypatch, repo_with_branch, "_get_branch_diff",
            "/api/branch-diff?branch=feature/x",
        )
        assert captured["status"] == 200
        assert captured["body"] == gs.build_branch_diff(repo_with_branch, "feature/x")

    def test_branch_diff_400_parity(self, monkeypatch, repo: Path) -> None:
        """Missing ?branch= → handler maps the builder ValueError to 400."""
        captured = self._invoke(monkeypatch, repo, "_get_branch_diff", "/api/branch-diff")
        assert captured["status"] == 400
        assert captured["body"] == {"error": "invalid branch name"}


# ---------------------------------------------------------------------------
# Cross-SERVER error-body parity: FastAPI route body == legacy handler body
# ---------------------------------------------------------------------------

class TestErrorBodyCrossServerParity:
    """The FastAPI seam must emit the SAME error body shape as the legacy stdlib
    handler — ``{"error": <msg>}``, not FastAPI's default ``{"detail": ...}`` —
    so a flip from one server to the other doesn't silently change what the
    frontend sees on the error paths.

    Asserts the full JSON body (key name AND message) is equal across both
    servers on the same fixture, for branch-diff 400 and branches 500.
    """

    @staticmethod
    def _legacy_body(monkeypatch, ws_root: Path, method_name: str, path: str) -> dict:
        import vivarium_dashboard.server as server

        monkeypatch.setattr(server, "WORKSPACE", ws_root)
        handler = server.Handler.__new__(server.Handler)
        captured: dict = {}

        def _fake_json(data, code):
            captured["body"] = data
            captured["status"] = code

        handler._json = _fake_json          # type: ignore[method-assign]
        handler.path = path
        getattr(handler, method_name)()
        return captured

    @staticmethod
    def _fastapi_client(ws_root: Path):
        from fastapi.testclient import TestClient

        from vivarium_dashboard.api.app import create_app, get_workspace

        app = create_app()
        app.dependency_overrides[get_workspace] = lambda: ws_root
        return TestClient(app)

    def test_branch_diff_400_body_matches(self, monkeypatch, repo: Path) -> None:
        # Missing ?branch= on the same repo, both servers.
        legacy = self._legacy_body(
            monkeypatch, repo, "_get_branch_diff", "/api/branch-diff"
        )
        fastapi = self._fastapi_client(repo).get("/api/branch-diff")
        assert legacy["status"] == fastapi.status_code == 400
        assert legacy["body"] == fastapi.json() == {"error": "invalid branch name"}

    def test_branches_500_body_matches(self, monkeypatch, tmp_path: Path) -> None:
        # A non-git dir makes list_branches return an {"error": ...} dict → 500.
        legacy = self._legacy_body(
            monkeypatch, tmp_path, "_serve_branches", "/api/branches"
        )
        fastapi = self._fastapi_client(tmp_path).get("/api/branches")
        assert legacy["status"] == fastapi.status_code == 500
        # Same builder + same dir → identical git error string under "error".
        assert legacy["body"] == fastapi.json()
        assert set(fastapi.json()) == {"error"}


# ---------------------------------------------------------------------------
# diagnose_push_error: parity vs the server copy (C-state-3f2 extraction)
# ---------------------------------------------------------------------------

class TestDiagnosePushError:
    """``git_status.diagnose_push_error`` is a verbatim copy of the pure
    ``server._diagnose_push_error``; assert byte-identical output across the
    representative branches + the None tails."""

    _CASES = [
        "",
        "fatal: 'origin' does not appear to be a git repository",
        "fatal: Could not read from remote repository.",
        "ERROR: Permission to owner/repo.git denied to user.",
        "! [rejected]  feat -> feat (non-fast-forward)",
        "! [rejected]  feat -> feat (fetch first, you are behind)",
        "some unrelated error string with no known pattern",
    ]

    def test_parity_vs_server(self) -> None:
        import vivarium_dashboard.server as server
        for err in self._CASES:
            assert gs.diagnose_push_error(err) == server._diagnose_push_error(err)

    def test_no_origin_body(self) -> None:
        d = gs.diagnose_push_error("fatal: Could not read from remote repository.")
        assert d == {
            "category": "no_origin",
            "summary": "Push failed because no GitHub remote is configured.",
            "suggestion": "Click `Create GitHub repo` in the workstream strip to create one and push in one step.",
        }

    def test_auth_and_behind_and_none(self) -> None:
        assert gs.diagnose_push_error("Permission to x denied")["category"] == "auth"
        assert gs.diagnose_push_error("[rejected] non-fast-forward")["category"] == "behind"
        assert gs.diagnose_push_error("") is None
        assert gs.diagnose_push_error("nope") is None

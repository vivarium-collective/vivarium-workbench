"""Parity + unit tests for the git-subprocess commit/push builders.

Covers the pure ``lib.git_commit_views`` builders (``branch_push`` /
``dirty_commit_all``) AND the ``lib.git_status`` extractions they reuse
(``remote_commit_and_push`` / ``suggest_dirty_commit_message`` / the
``NotAGitRepo`` sentinel).

Every test monkeypatches ``subprocess.run`` and the reused lib fns, so **no test
ever runs real git** — the dirty-commit sequence in particular is destructive and
is exercised entirely against a recording fake.  The byte-identical reproduction
of the legacy handlers is asserted via the captured git argv + the exact
``(dict, status)`` mapping; ``suggest_dirty_commit_message`` is additionally
compared against the still-present ``server`` copy.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vivarium_dashboard.lib import git_commit_views as gcv
from vivarium_dashboard.lib import git_status as gs


# ===========================================================================
# Recording fake for subprocess.run — maps an argv to a canned result and
# raises CalledProcessError when ``check=True`` meets a non-zero returncode
# (matching real subprocess.run semantics).
# ===========================================================================
class _FakeRun:
    def __init__(self, responses):
        """``responses``: list of (predicate(argv)->bool, result_dict)."""
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, argv, *args, **kwargs):
        self.calls.append(argv)
        for pred, spec in self.responses:
            if pred(argv):
                rc = spec.get("returncode", 0)
                stdout = spec.get("stdout", "")
                stderr = spec.get("stderr", "")
                if kwargs.get("check") and rc != 0:
                    raise subprocess.CalledProcessError(rc, argv, output=stdout, stderr=stderr)
                return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr=stderr)
        raise AssertionError(f"unexpected subprocess argv: {argv}")


def _has(*needles):
    return lambda argv: all(n in argv for n in needles)


# ===========================================================================
# branch_push — delegates to git_status.remote_commit_and_push
# ===========================================================================
class TestBranchPush:
    def test_happy_path_200(self, monkeypatch):
        captured = {}

        def _fake(ws_root, message):
            captured["ws_root"] = ws_root
            captured["message"] = message
            return {"ok": True, "pushed": True, "commit": "abc123", "branch": "feat/x"}

        monkeypatch.setattr(gcv.git_status, "remote_commit_and_push", _fake)
        body, code = gcv.branch_push(Path("/ws"), {"message": "my msg"})
        assert code == 200
        assert body == {"ok": True, "pushed": True, "commit": "abc123", "branch": "feat/x"}
        assert captured == {"ws_root": Path("/ws"), "message": "my msg"}

    def test_default_message_when_omitted(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            gcv.git_status, "remote_commit_and_push",
            lambda ws, msg: seen.setdefault("msg", msg) or {"ok": True, "pushed": False, "commit": "x", "branch": "b"},
        )
        gcv.branch_push(Path("/ws"), {})
        assert seen["msg"] == "dashboard commit"
        # None body also falls back
        seen.clear()
        gcv.branch_push(Path("/ws"), None)
        assert seen["msg"] == "dashboard commit"
        # empty string falls back too
        seen.clear()
        gcv.branch_push(Path("/ws"), {"message": ""})
        assert seen["msg"] == "dashboard commit"

    def test_not_a_git_repo_409(self, monkeypatch):
        def _raise(ws, msg):
            raise gs.NotAGitRepo("active source is not a git workspace (no commit/push)")

        monkeypatch.setattr(gcv.git_status, "remote_commit_and_push", _raise)
        body, code = gcv.branch_push(Path("/ws"), {"message": "m"})
        assert code == 409
        assert body == {"error": "active source is not a git workspace (no commit/push)"}

    def test_other_error_500(self, monkeypatch):
        def _raise(ws, msg):
            raise RuntimeError("git push failed: boom")

        monkeypatch.setattr(gcv.git_status, "remote_commit_and_push", _raise)
        body, code = gcv.branch_push(Path("/ws"), {"message": "m"})
        assert code == 500
        assert body == {"error": "git push failed: boom"}


# ===========================================================================
# dirty_commit_all — reproduces the legacy git sequence with cwd=ws_root
# ===========================================================================
class TestDirtyCommitAll:
    def test_no_active_workstream_409(self, monkeypatch):
        monkeypatch.setattr(gcv.work_state, "load_state_or_adopt_current", lambda: {})
        body, code = gcv.dirty_commit_all(Path("/ws"), {})
        assert code == 409
        assert body == {"error": "no active workstream"}

    def test_rev_parse_failure_500(self, monkeypatch):
        monkeypatch.setattr(
            gcv.work_state, "load_state_or_adopt_current", lambda: {"active_branch": "feat/x"}
        )
        fake = _FakeRun([
            (_has("rev-parse", "--abbrev-ref"), {"returncode": 128, "stderr": b"fatal: bad\n"}),
        ])
        monkeypatch.setattr(gcv.subprocess, "run", fake)
        body, code = gcv.dirty_commit_all(Path("/ws"), {})
        assert code == 500
        assert body == {"error": "git rev-parse failed: fatal: bad\n"}

    def test_checkout_failure_500(self, monkeypatch):
        monkeypatch.setattr(
            gcv.work_state, "load_state_or_adopt_current", lambda: {"active_branch": "feat/x"}
        )
        fake = _FakeRun([
            (_has("rev-parse", "--abbrev-ref"), {"stdout": "main"}),  # current != branch
            (_has("checkout", "feat/x"), {"returncode": 1, "stderr": "error: cannot checkout"}),
        ])
        monkeypatch.setattr(gcv.subprocess, "run", fake)
        body, code = gcv.dirty_commit_all(Path("/ws"), {})
        assert code == 500
        assert body == {"error": "could not check out 'feat/x': error: cannot checkout"}

    def test_already_clean_409(self, monkeypatch):
        monkeypatch.setattr(
            gcv.work_state, "load_state_or_adopt_current", lambda: {"active_branch": "feat/x"}
        )
        fake = _FakeRun([
            (_has("rev-parse", "--abbrev-ref"), {"stdout": "feat/x"}),  # current == branch
        ])
        monkeypatch.setattr(gcv.subprocess, "run", fake)
        monkeypatch.setattr(gcv.git_status, "dirty_workspace", lambda ws: "   ")
        body, code = gcv.dirty_commit_all(Path("/ws"), {})
        assert code == 409
        assert body == {"error": "working tree is already clean"}

    def test_happy_path_200_uses_identity_flags_and_reports_reset(self, monkeypatch):
        monkeypatch.setattr(
            gcv.work_state, "load_state_or_adopt_current", lambda: {"active_branch": "feat/x"}
        )
        fake = _FakeRun([
            (_has("rev-parse", "--abbrev-ref"), {"stdout": "feat/x"}),  # already on branch
            (_has("add", "-A"), {}),
            (_has("reset", "HEAD", "--", "reports/"), {}),
            (_has("commit", "-m"), {}),
            (lambda a: a[:3] == ["git", "rev-parse", "HEAD"], {"stdout": "0123456789abcdef"}),
        ])
        monkeypatch.setattr(gcv.subprocess, "run", fake)
        # NB: the legacy handler does ``dirty_workspace(...).strip()``, which
        # would clip a leading-space porcelain first line — so use staged-style
        # ("M  path") porcelain whose col0 is non-space (strip is a no-op on it).
        monkeypatch.setattr(
            gcv.git_status, "dirty_workspace",
            lambda ws: "M  scripts/a.py\nM  scripts/b.py",
        )
        monkeypatch.setattr(
            gcv.git_status, "suggest_dirty_commit_message",
            lambda paths: "chore(scripts): commit 2 pending files",
        )
        body, code = gcv.dirty_commit_all(Path("/ws"), {})
        assert code == 200
        assert body == {
            "commit_sha": "0123456",  # sha[:7]
            "message": "chore(scripts): commit 2 pending files",
            "paths": ["scripts/a.py", "scripts/b.py"],
        }
        # reports/ reset ran
        assert any(
            a[:5] == ["git", "reset", "HEAD", "--", "reports/"] for a in fake.calls
        )
        # commit used the pbg-template user.email + user.name flags
        commit_call = next(a for a in fake.calls if "commit" in a)
        assert commit_call == [
            "git", "-c", "user.email=pbg-template@local",
                  "-c", "user.name=pbg-template",
                  "commit", "-m", "chore(scripts): commit 2 pending files",
        ]
        # checkout was NOT invoked (current == branch)
        assert not any("checkout" in a for a in fake.calls)

    def test_checkout_runs_when_head_differs(self, monkeypatch):
        monkeypatch.setattr(
            gcv.work_state, "load_state_or_adopt_current", lambda: {"active_branch": "feat/x"}
        )
        fake = _FakeRun([
            (_has("rev-parse", "--abbrev-ref"), {"stdout": "main"}),  # differs → checkout
            (_has("checkout", "feat/x"), {"returncode": 0}),
            (_has("add", "-A"), {}),
            (_has("reset", "HEAD", "--", "reports/"), {}),
            (_has("commit", "-m"), {}),
            (lambda a: a[:3] == ["git", "rev-parse", "HEAD"], {"stdout": "abcdef0"}),
        ])
        monkeypatch.setattr(gcv.subprocess, "run", fake)
        monkeypatch.setattr(gcv.git_status, "dirty_workspace", lambda ws: "M  docs/x.md")
        monkeypatch.setattr(
            gcv.git_status, "suggest_dirty_commit_message", lambda paths: "docs: commit 1 pending file"
        )
        body, code = gcv.dirty_commit_all(Path("/ws"), {})
        assert code == 200
        assert any("checkout" in a for a in fake.calls)
        assert body["paths"] == ["docs/x.md"]

    def test_git_operation_failure_500(self, monkeypatch):
        monkeypatch.setattr(
            gcv.work_state, "load_state_or_adopt_current", lambda: {"active_branch": "feat/x"}
        )
        fake = _FakeRun([
            (_has("rev-parse", "--abbrev-ref"), {"stdout": "feat/x"}),
            (_has("add", "-A"), {}),
            (_has("reset", "HEAD", "--", "reports/"), {}),
            (_has("commit", "-m"), {"returncode": 1, "stderr": b"nothing to commit\n"}),
        ])
        monkeypatch.setattr(gcv.subprocess, "run", fake)
        monkeypatch.setattr(gcv.git_status, "dirty_workspace", lambda ws: " M scripts/a.py")
        monkeypatch.setattr(
            gcv.git_status, "suggest_dirty_commit_message", lambda paths: "chore(scripts): commit 1 pending file"
        )
        body, code = gcv.dirty_commit_all(Path("/ws"), {})
        assert code == 500
        assert body == {"error": "git operation failed: nothing to commit\n"}

    def test_ws_root_threaded_as_cwd(self, monkeypatch):
        """Every git subprocess in the dirty-commit flow runs with cwd=ws_root."""
        monkeypatch.setattr(
            gcv.work_state, "load_state_or_adopt_current", lambda: {"active_branch": "feat/x"}
        )
        seen_cwds: list = []

        class _CwdRun(_FakeRun):
            def __call__(self, argv, *args, **kwargs):
                seen_cwds.append(kwargs.get("cwd"))
                return super().__call__(argv, *args, **kwargs)

        fake = _CwdRun([
            (_has("rev-parse", "--abbrev-ref"), {"stdout": "feat/x"}),
            (_has("add", "-A"), {}),
            (_has("reset", "HEAD", "--", "reports/"), {}),
            (_has("commit", "-m"), {}),
            (lambda a: a[:3] == ["git", "rev-parse", "HEAD"], {"stdout": "deadbee"}),
        ])
        monkeypatch.setattr(gcv.subprocess, "run", fake)
        monkeypatch.setattr(gcv.git_status, "dirty_workspace", lambda ws: " M scripts/a.py")
        monkeypatch.setattr(
            gcv.git_status, "suggest_dirty_commit_message", lambda paths: "m"
        )
        gcv.dirty_commit_all(Path("/myws"), {})
        assert seen_cwds and all(c == Path("/myws") for c in seen_cwds)


# ===========================================================================
# git_status extractions — remote_commit_and_push + suggest_dirty_commit_message
# ===========================================================================
class TestRemoteCommitAndPush:
    def test_not_a_repo_raises_NotAGitRepo(self, monkeypatch):
        fake = _FakeRun([
            (_has("rev-parse", "--is-inside-work-tree"), {"returncode": 128, "stdout": ""}),
        ])
        monkeypatch.setattr(gs, "subprocess", _FakeSubprocess(fake))
        with pytest.raises(gs.NotAGitRepo) as ei:
            gs.remote_commit_and_push(Path("/ws"), "msg")
        assert str(ei.value) == "active source is not a git workspace (no commit/push)"

    def test_non_true_stdout_raises_NotAGitRepo(self, monkeypatch):
        fake = _FakeRun([
            (_has("rev-parse", "--is-inside-work-tree"), {"returncode": 0, "stdout": "false\n"}),
        ])
        monkeypatch.setattr(gs, "subprocess", _FakeSubprocess(fake))
        with pytest.raises(gs.NotAGitRepo):
            gs.remote_commit_and_push(Path("/ws"), "msg")

    def test_clean_tree_pushed_false(self, monkeypatch):
        fake = _FakeRun([
            (_has("rev-parse", "--is-inside-work-tree"), {"stdout": "true\n"}),
            (_has("add", "-A"), {}),
            (_has("status", "--porcelain"), {"stdout": "   \n"}),  # strip() → empty → clean
            (_has("rev-parse", "--abbrev-ref"), {"stdout": "main\n"}),
        ])
        monkeypatch.setattr(gs, "subprocess", _FakeSubprocess(fake))
        monkeypatch.setattr(gs, "remote_push_and_sha", lambda ws: "deadbeefsha")
        result = gs.remote_commit_and_push(Path("/ws"), "msg")
        assert result == {"ok": True, "pushed": False, "commit": "deadbeefsha", "branch": "main"}
        # commit must NOT have been invoked on a clean tree
        assert not any("commit" in a for a in fake.calls)

    def test_dirty_tree_commits_and_pushes(self, monkeypatch):
        fake = _FakeRun([
            (_has("rev-parse", "--is-inside-work-tree"), {"stdout": "true\n"}),
            (_has("add", "-A"), {}),
            (_has("status", "--porcelain"), {"stdout": " M a.py\n"}),
            (_has("commit", "-m"), {"returncode": 0}),
            (_has("rev-parse", "--abbrev-ref"), {"stdout": "feat/y\n"}),
        ])
        monkeypatch.setattr(gs, "subprocess", _FakeSubprocess(fake))
        monkeypatch.setattr(gs, "remote_push_and_sha", lambda ws: "cafef00d")
        result = gs.remote_commit_and_push(Path("/ws"), "do it")
        assert result == {"ok": True, "pushed": True, "commit": "cafef00d", "branch": "feat/y"}
        commit_call = next(a for a in fake.calls if "commit" in a)
        assert commit_call == ["git", "-C", "/ws", "commit", "-m", "do it"]

    def test_empty_message_falls_back_to_dashboard_commit(self, monkeypatch):
        fake = _FakeRun([
            (_has("rev-parse", "--is-inside-work-tree"), {"stdout": "true"}),
            (_has("add", "-A"), {}),
            (_has("status", "--porcelain"), {"stdout": " M a.py"}),
            (_has("commit", "-m"), {"returncode": 0}),
            (_has("rev-parse", "--abbrev-ref"), {"stdout": "main"}),
        ])
        monkeypatch.setattr(gs, "subprocess", _FakeSubprocess(fake))
        monkeypatch.setattr(gs, "remote_push_and_sha", lambda ws: "sha")
        gs.remote_commit_and_push(Path("/ws"), "")
        commit_call = next(a for a in fake.calls if "commit" in a)
        assert commit_call[-1] == "dashboard commit"

    def test_commit_failure_raises_with_tail(self, monkeypatch):
        long_err = "x" * 500
        fake = _FakeRun([
            (_has("rev-parse", "--is-inside-work-tree"), {"stdout": "true"}),
            (_has("add", "-A"), {}),
            (_has("status", "--porcelain"), {"stdout": " M a.py"}),
            (_has("commit", "-m"), {"returncode": 1, "stderr": long_err}),
        ])
        monkeypatch.setattr(gs, "subprocess", _FakeSubprocess(fake))
        with pytest.raises(RuntimeError) as ei:
            gs.remote_commit_and_push(Path("/ws"), "m")
        # [-300:] tail
        assert str(ei.value) == f"git commit failed: {long_err[-300:]}"


class _FakeSubprocess:
    """Stand-in for the ``subprocess`` module exposing only ``run`` + the real
    ``CalledProcessError`` / ``CompletedProcess`` (so ``check=True`` semantics and
    ``except subprocess.CalledProcessError`` keep working)."""

    CalledProcessError = subprocess.CalledProcessError
    CompletedProcess = subprocess.CompletedProcess

    def __init__(self, run):
        self.run = run


class TestSuggestDirtyCommitMessageParity:
    @pytest.mark.parametrize("paths", [
        [],
        ["scripts/a.py"],
        ["scripts/a.py", "scripts/b.py"],
        ["docs/x.md", "docs/y.md"],
        ["composites/c.yaml"],
        ["investigations/i.yaml"],
        ["tests/t.py"],
        ["reports/r.html"],
        ["pbg_chromosome_rep1/mod.py"],
        ["weird_dir/file.py", "weird_dir/two.py"],
        ["scripts/a.py", "docs/x.md"],  # multiple top dirs → generic chore:
        ["topfile.py"],  # single top dir == the file's own segment
    ])
    def test_parity_vs_server_copy(self, paths):
        import vivarium_dashboard.server as server

        assert gs.suggest_dirty_commit_message(paths) == server._suggest_dirty_commit_message(paths)

    def test_known_categories(self):
        assert gs.suggest_dirty_commit_message([]) == "chore: commit pending files"
        assert (
            gs.suggest_dirty_commit_message(["scripts/a.py", "scripts/b.py"])
            == "chore(scripts): commit 2 pending files"
        )
        assert gs.suggest_dirty_commit_message(["docs/x.md"]) == "docs: commit 1 pending file"
        assert (
            gs.suggest_dirty_commit_message(["a/x", "b/y"]) == "chore: commit 2 pending files"
        )

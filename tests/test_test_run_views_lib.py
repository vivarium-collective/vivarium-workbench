"""Behavioural tests for the P1 test-run lib builders.

``lib.test_run_views`` reproduces the two stdlib test-running handlers
(``_post_study_tests_run`` / ``_post_run_tests``) byte-identically. These tests
are hermetic: ``run_study_tests`` and ``subprocess.run`` are always
monkeypatched, so NO real pytest / subprocess ever runs.
"""

import subprocess

import pytest

from vivarium_dashboard.lib import test_run_views


def _make_ws(tmp_path):
    """A minimal workspace root (default layout: studies/, tests/)."""
    (tmp_path / "workspace.yaml").write_text("name: demo-ws\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# study_tests_run
# ---------------------------------------------------------------------------
class TestStudyTestsRun:
    def test_missing_study_400(self, tmp_path):
        ws = _make_ws(tmp_path)
        assert test_run_views.study_tests_run(ws, {}) == (
            {"error": "missing 'study' in body"}, 400,
        )

    def test_none_body_400(self, tmp_path):
        ws = _make_ws(tmp_path)
        assert test_run_views.study_tests_run(ws, None) == (
            {"error": "missing 'study' in body"}, 400,
        )

    def test_study_not_found_404(self, tmp_path):
        ws = _make_ws(tmp_path)
        body, status = test_run_views.study_tests_run(ws, {"study": "ghost"})
        assert status == 404
        assert body == {"error": "study not found: ghost"}

    def test_happy_path_200(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        spec = ws / "studies" / "s1" / "study.yaml"
        spec.parent.mkdir(parents=True)
        spec.write_text("baseline: []\n", encoding="utf-8")

        class _Result:
            summary = {"passed": 2, "failed": 0, "skipped": 1, "duration_s": 0.4}
            tests = [{"nodeid": "t::a", "outcome": "passed"}]
            note = "ran 3"

        from vivarium_dashboard.lib import study_tests
        captured = {}

        def _fake(workspace, slug):
            captured["args"] = (workspace, slug)
            return _Result()

        monkeypatch.setattr(study_tests, "run_study_tests", _fake)
        body, status = test_run_views.study_tests_run(ws, {"study": "s1"})
        assert status == 200
        assert body == {
            "summary": {"passed": 2, "failed": 0, "skipped": 1, "duration_s": 0.4},
            "tests": [{"nodeid": "t::a", "outcome": "passed"}],
            "note": "ran 3",
        }
        # ws_root threaded through verbatim (replaces the server WORKSPACE global).
        assert captured["args"] == (ws, "s1")

    def test_concurrent_409(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        spec = ws / "studies" / "s1" / "study.yaml"
        spec.parent.mkdir(parents=True)
        spec.write_text("baseline: []\n", encoding="utf-8")

        from vivarium_dashboard.lib import study_tests

        def _raise(workspace, slug):
            raise study_tests.StudyTestsConcurrentError(
                "tests already running for study 's1'"
            )

        monkeypatch.setattr(study_tests, "run_study_tests", _raise)
        body, status = test_run_views.study_tests_run(ws, {"study": "s1"})
        assert status == 409
        assert body == {"error": "tests already running for study 's1'"}


# ---------------------------------------------------------------------------
# run_workspace_tests
# ---------------------------------------------------------------------------
class TestRunWorkspaceTests:
    def test_happy_path_200(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="3 passed", stderr="",
            )

        monkeypatch.setattr(test_run_views.subprocess, "run", _fake_run)
        body, status = test_run_views.run_workspace_tests(ws, {})
        assert status == 200
        assert body == {"returncode": 0, "stdout": "3 passed", "stderr": ""}
        # Byte-identical cmd/kwargs to the legacy handler.
        assert captured["cmd"][1:4] == ["-m", "pytest", "-v"]
        assert captured["kwargs"]["cwd"] == ws
        assert captured["kwargs"]["timeout"] == 120
        assert captured["kwargs"]["capture_output"] is True
        assert captured["kwargs"]["text"] is True

    def test_nonzero_returncode_still_200(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)

        def _fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="out", stderr="err",
            )

        monkeypatch.setattr(test_run_views.subprocess, "run", _fake_run)
        assert test_run_views.run_workspace_tests(ws, {}) == (
            {"returncode": 1, "stdout": "out", "stderr": "err"}, 200,
        )

    def test_timeout_500(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)

        def _timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)

        monkeypatch.setattr(test_run_views.subprocess, "run", _timeout)
        assert test_run_views.run_workspace_tests(ws, {}) == (
            {"error": "pytest timed out after 120s"}, 500,
        )

    def test_generic_exception_500(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)

        def _boom(cmd, **kwargs):
            raise RuntimeError("no python")

        monkeypatch.setattr(test_run_views.subprocess, "run", _boom)
        assert test_run_views.run_workspace_tests(ws, {}) == (
            {"error": "no python"}, 500,
        )

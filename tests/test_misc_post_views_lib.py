"""Behavioural tests for the misc-POST lib builders.

``lib.misc_post_views`` reproduces 3 stdlib handlers (``_post_suggest`` /
``_post_study_report_single`` / ``_post_open_window``) byte-identically. These
tests are hermetic: ``subprocess.run`` (git-log / browser-open),
``suggest_requests.write_request``, ``work_state.load_state`` and
``single_study_report.build_single_study_report_for_test`` are always
monkeypatched — so NO real git ever runs and NO real window is ever opened.
"""

import json
import subprocess

import pytest

from vivarium_dashboard.lib import misc_post_views
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def _make_ws(tmp_path, name="demo-ws", description="a demo"):
    (tmp_path / "workspace.yaml").write_text(
        f"name: {name}\ndescription: {description}\n", encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# suggest
# ---------------------------------------------------------------------------
class TestSuggest:
    def test_invalid_kind_400(self, tmp_path):
        ws = _make_ws(tmp_path)
        body, status = misc_post_views.suggest(ws, {"kind": "bogus"})
        assert status == 400
        assert body == {
            "error": "invalid kind (must be one of "
                     "('repo-name', 'pr-title', 'pr-body'))"
        }

    def test_empty_kind_400(self, tmp_path):
        ws = _make_ws(tmp_path)
        body, status = misc_post_views.suggest(ws, {})
        assert status == 400
        assert "invalid kind" in body["error"]

    def test_happy_path_200_shape_and_context(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path, name="my-ws", description="my desc")
        captured = {}

        def _fake_write(ws_root, kind, context):
            captured["args"] = (ws_root, kind, context)
            return "repo-name-1700000000"

        monkeypatch.setattr(
            misc_post_views.suggest_requests, "write_request", _fake_write)
        monkeypatch.setattr(
            misc_post_views.work_state, "load_state",
            lambda: {"active_branch": "feat/x"})

        # 35 commit lines → context must cap at 30.
        lines = "\n".join(f"abc{i:03d} commit {i}" for i in range(35))

        def _fake_run(cmd, **kwargs):
            assert cmd == ["git", "log", "--format=%h %s", "main..feat/x"]
            assert kwargs["cwd"] == ws
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=lines + "\n", stderr="")

        monkeypatch.setattr(misc_post_views.subprocess, "run", _fake_run)

        body, status = misc_post_views.suggest(
            ws, {"kind": "repo-name", "context_extras": {"k": "v"}})
        assert status == 200
        assert body == {
            "ok": True,
            "id": "repo-name-1700000000",
            "skill_command": "/pbg-suggest repo-name-1700000000",
            "instructions": (
                "Open Claude Code in this workspace and run "
                "`/pbg-suggest repo-name-1700000000`. The dashboard will pick "
                "up the response automatically."
            ),
        }
        # write_request received ws_root, kind, and the built context.
        ws_root, kind, context = captured["args"]
        assert ws_root == ws
        assert kind == "repo-name"
        assert context["workspace_name"] == "my-ws"
        assert context["workspace_description"] == "my desc"
        assert context["active_branch"] == "feat/x"
        assert len(context["commits"]) == 30      # commits[:30] cap
        assert context["commits"][0] == "abc000 commit 0"
        assert context["extras"] == {"k": "v"}

    def test_happy_no_branch_skips_git(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        captured = {}
        monkeypatch.setattr(
            misc_post_views.suggest_requests, "write_request",
            lambda ws_root, kind, context: captured.update(context=context) or "id-1")
        monkeypatch.setattr(
            misc_post_views.work_state, "load_state", lambda: {})

        def _boom(*a, **k):
            raise AssertionError("git must not run when there is no active branch")

        monkeypatch.setattr(misc_post_views.subprocess, "run", _boom)
        body, status = misc_post_views.suggest(ws, {"kind": "pr-title"})
        assert status == 200
        assert captured["context"]["active_branch"] is None
        assert captured["context"]["commits"] == []
        assert captured["context"]["extras"] == {}


# ---------------------------------------------------------------------------
# study_report_single
# ---------------------------------------------------------------------------
class TestStudyReportSingle:
    def test_happy_passthrough(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        captured = {}

        def _fake(ws_root, body):
            captured["args"] = (ws_root, body)
            return {"html_path": "reports/s1.html", "size_bytes": 42,
                    "study": "s1"}, 200

        monkeypatch.setattr(
            misc_post_views.single_study_report,
            "build_single_study_report_for_test", _fake)
        body, status = misc_post_views.study_report_single(ws, {"study": "s1"})
        assert status == 200
        assert body == {"html_path": "reports/s1.html", "size_bytes": 42,
                        "study": "s1"}
        assert captured["args"] == (ws, {"study": "s1"})

    def test_builder_error_status_preserved(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        monkeypatch.setattr(
            misc_post_views.single_study_report,
            "build_single_study_report_for_test",
            lambda ws_root, body: ({"error": "either 'study' or 'investigation' "
                                    "is required"}, 400))
        body, status = misc_post_views.study_report_single(ws, {})
        assert status == 400
        assert body == {"error": "either 'study' or 'investigation' is required"}

    def test_exception_500(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)

        def _raise(ws_root, body):
            raise RuntimeError("render exploded")

        monkeypatch.setattr(
            misc_post_views.single_study_report,
            "build_single_study_report_for_test", _raise)
        body, status = misc_post_views.study_report_single(ws, {"study": "s1"})
        assert status == 500
        assert body == {"error": "render exploded"}


# ---------------------------------------------------------------------------
# open_window
# ---------------------------------------------------------------------------
class TestOpenWindow:
    def _info_path(self, ws):
        p = WorkspacePaths.load(ws).pbg / "server" / "server-info"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def test_no_server_info_503(self, tmp_path):
        ws = _make_ws(tmp_path)
        body, status = misc_post_views.open_window(ws, {"route": "/x"})
        assert status == 503
        assert body == {
            "error": "server-info file not found - is the dashboard running?"}

    def test_bad_json_500(self, tmp_path):
        ws = _make_ws(tmp_path)
        self._info_path(ws).write_text("{not json", encoding="utf-8")
        body, status = misc_post_views.open_window(ws, {})
        assert status == 500
        assert body["error"].startswith("server-info parse failed:")

    def test_unsupported_platform_501(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        self._info_path(ws).write_text(
            json.dumps({"url": "http://127.0.0.1:8765/"}), encoding="utf-8")
        monkeypatch.setattr(misc_post_views.platform, "system", lambda: "Plan9")
        body, status = misc_post_views.open_window(ws, {})
        assert status == 501
        assert body == {"error": "unsupported platform: plan9"}

    def test_happy_200(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        self._info_path(ws).write_text(
            json.dumps({"url": "http://127.0.0.1:8765/"}), encoding="utf-8")
        monkeypatch.setattr(misc_post_views.platform, "system", lambda: "Darwin")
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(misc_post_views.subprocess, "run", _fake_run)
        body, status = misc_post_views.open_window(ws, {"route": "composites/x"})
        assert status == 200
        # route gets a leading slash; url = base.rstrip('/') + route
        assert body == {"ok": True, "url": "http://127.0.0.1:8765/composites/x"}
        assert captured["cmd"] == ["open", "http://127.0.0.1:8765/composites/x"]
        assert captured["kwargs"]["timeout"] == 5
        assert captured["kwargs"]["capture_output"] is True

    def test_linux_uses_xdg_open(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        self._info_path(ws).write_text(
            json.dumps({"url": "http://h/"}), encoding="utf-8")
        monkeypatch.setattr(misc_post_views.platform, "system", lambda: "Linux")
        captured = {}
        monkeypatch.setattr(
            misc_post_views.subprocess, "run",
            lambda cmd, **k: captured.update(cmd=cmd) or
            subprocess.CompletedProcess(args=cmd, returncode=0))
        body, status = misc_post_views.open_window(ws, {})
        assert status == 200
        assert captured["cmd"] == ["xdg-open", "http://h/"]

    def test_open_failure_500(self, tmp_path, monkeypatch):
        ws = _make_ws(tmp_path)
        self._info_path(ws).write_text(
            json.dumps({"url": "http://h/"}), encoding="utf-8")
        monkeypatch.setattr(misc_post_views.platform, "system", lambda: "Darwin")

        def _boom(cmd, **kwargs):
            raise OSError("open binary missing")

        monkeypatch.setattr(misc_post_views.subprocess, "run", _boom)
        body, status = misc_post_views.open_window(ws, {})
        assert status == 500
        assert body == {"error": "open failed: open binary missing"}

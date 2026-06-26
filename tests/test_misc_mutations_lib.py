"""Behavioural parity tests for ``lib.misc_mutations`` (3 builders).

Ports of the stdlib handlers ``_post_click`` / ``_post_render`` /
``_post_feedback_import``.  ``record_click`` is exercised against a real tmp
``ws_root`` (asserting the events-file lines); ``render_dashboard`` monkeypatches
``misc_mutations.render_workspace_report``; ``feedback_import`` monkeypatches the
pbg writer (success / FeedbackImportError 400 / Exception 500) and simulates the
ImportError 500 path.  No test touches a real workspace report or the real pbg
feedback dir.
"""

from __future__ import annotations

import json
import sys

import pytest

from vivarium_dashboard.lib import misc_mutations as mm


# ---------------------------------------------------------------------------
# record_click
# ---------------------------------------------------------------------------
class TestRecordClick:
    def _events_file(self, ws_root):
        return ws_root / ".pbg" / "server" / "state" / "events"

    def test_appends_json_line(self, tmp_path):
        body = {"event": "view", "study": "dnaa-00"}
        assert mm.record_click(tmp_path, body) is None
        ev = self._events_file(tmp_path)
        assert ev.is_file()
        lines = ev.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == body

    def test_second_call_appends_second_line(self, tmp_path):
        mm.record_click(tmp_path, {"n": 1})
        mm.record_click(tmp_path, {"n": 2})
        ev = self._events_file(tmp_path)
        lines = ev.read_text().splitlines()
        assert len(lines) == 2
        assert [json.loads(line) for line in lines] == [{"n": 1}, {"n": 2}]

    def test_creates_parent_dirs(self, tmp_path):
        # No .pbg/server/state tree exists yet — mkdir(parents=True) creates it.
        assert not (tmp_path / ".pbg").exists()
        mm.record_click(tmp_path, {"x": 1})
        assert self._events_file(tmp_path).is_file()


# ---------------------------------------------------------------------------
# render_dashboard
# ---------------------------------------------------------------------------
class TestRenderDashboard:
    def test_happy_200(self, tmp_path, monkeypatch):
        seen = {}

        def _fake(ws_root):
            seen["ws"] = ws_root

        monkeypatch.setattr(mm, "render_workspace_report", _fake)
        assert mm.render_dashboard(tmp_path) == ({"ok": True}, 200)
        assert seen["ws"] == tmp_path

    def test_render_failure_500(self, tmp_path, monkeypatch):
        def _raise(ws_root):
            raise RuntimeError("template boom")

        monkeypatch.setattr(mm, "render_workspace_report", _raise)
        body, status = mm.render_dashboard(tmp_path)
        assert status == 500
        assert body == {"error": "template boom"}


# ---------------------------------------------------------------------------
# feedback_import
# ---------------------------------------------------------------------------
def _payload():
    return {
        "meta": {"investigation": "dnaa"},
        "annotations": {
            "study-a": [{"text": "one"}, {"text": "two"}],
            "study-b": [{"text": "three"}],
            "ignored-non-list": "nope",
            "empty": None,
        },
    }


class TestFeedbackImport:
    def test_happy_200_with_n_entries(self, tmp_path, monkeypatch):
        import pbg_superpowers.feedback_import as fbm

        target = tmp_path / "investigations" / "dnaa" / "feedback" / "ts.yaml"

        def _fake_write(workspace, payload):
            return target

        monkeypatch.setattr(fbm, "write_feedback_payload", _fake_write)
        body, status = mm.feedback_import(tmp_path, _payload())
        assert status == 200
        # 2 (study-a) + 1 (study-b); non-list + None values excluded.
        assert body == {
            "ok": True,
            "path": "investigations/dnaa/feedback/ts.yaml",
            "n_entries": 3,
        }

    def test_feedback_import_error_400(self, tmp_path, monkeypatch):
        import pbg_superpowers.feedback_import as fbm

        def _raise(workspace, payload):
            raise fbm.FeedbackImportError("meta.investigation required")

        monkeypatch.setattr(fbm, "write_feedback_payload", _raise)
        assert mm.feedback_import(tmp_path, _payload()) == (
            {"error": "meta.investigation required"}, 400)

    def test_other_exception_500(self, tmp_path, monkeypatch):
        import pbg_superpowers.feedback_import as fbm

        def _raise(workspace, payload):
            raise RuntimeError("disk full")

        monkeypatch.setattr(fbm, "write_feedback_payload", _raise)
        assert mm.feedback_import(tmp_path, _payload()) == (
            {"error": "feedback import failed: disk full"}, 500)

    def test_import_error_500(self, tmp_path, monkeypatch):
        # Simulate pbg-superpowers being unavailable: making the module entry
        # None forces ``from pbg_superpowers.feedback_import import ...`` to
        # raise ImportError, which the builder maps to the verbatim 500 message.
        monkeypatch.setitem(sys.modules, "pbg_superpowers.feedback_import", None)
        assert mm.feedback_import(tmp_path, _payload()) == (
            {"error": "pbg-superpowers not available for feedback import"}, 500)

"""Behavioural parity tests for ``lib.misc_mutations`` (2 builders).

Ports of the stdlib handlers ``_post_click`` / ``_post_render``.
``record_click`` is exercised against a real tmp ``ws_root`` (asserting the
events-file lines); ``render_dashboard`` monkeypatches
``misc_mutations.render_workspace_report``.  No test touches a real workspace
report.
"""

from __future__ import annotations

import json

import pytest
import yaml

from vivarium_workbench.lib import misc_mutations as mm


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

    # -- Phase 2.1d: `today` threading -----------------------------------

    def test_no_body_still_calls_one_arg_form(self, tmp_path, monkeypatch):
        """Back-compat: an absent/empty body must call render_workspace_report
        with ONLY ws_root — a legacy one-arg fake (as used above) must keep
        working."""
        seen = {}

        def _fake(ws_root):
            seen["ws"] = ws_root

        monkeypatch.setattr(mm, "render_workspace_report", _fake)
        assert mm.render_dashboard(tmp_path, {}) == ({"ok": True}, 200)
        assert seen["ws"] == tmp_path

    def test_today_forwarded_when_given(self, tmp_path, monkeypatch):
        seen = {}

        def _fake(ws_root, **kwargs):
            seen["ws"] = ws_root
            seen["kwargs"] = kwargs

        monkeypatch.setattr(mm, "render_workspace_report", _fake)
        body, status = mm.render_dashboard(tmp_path, {"today": "2026-01-01"})
        assert (body, status) == ({"ok": True}, 200)
        assert seen["kwargs"] == {"today": "2026-01-01"}

    def test_falsy_today_omits_kwarg(self, tmp_path, monkeypatch):
        seen = {}

        def _fake(ws_root, **kwargs):
            seen["kwargs"] = kwargs

        monkeypatch.setattr(mm, "render_workspace_report", _fake)
        mm.render_dashboard(tmp_path, {"today": ""})
        assert seen["kwargs"] == {}


# ---------------------------------------------------------------------------
# render_dashboard(force=True) — override-log write (Phase 2.1d)
# ---------------------------------------------------------------------------
class TestRenderDashboardForce:
    """``force: true`` mirrors the old skill's forced-render path: BEFORE
    rendering, every currently-blocking report-lint finding is logged to
    ``.pbg/report-lint-overrides.json``. ``render_workspace_report`` itself is
    monkeypatched to a no-op — no test touches a real workspace report."""

    def _ws_with_blocking_finding(self, tmp_path):
        """A study with `evaluation_status: evaluated` and no
        `conclusion_logic` deterministically trips the linter's
        `incomplete_summaries` error check — no composite build required."""
        ws = tmp_path / "ws"
        sd = ws / "studies" / "s1"
        sd.mkdir(parents=True)
        (ws / "workspace.yaml").write_text("name: ws\n")
        spec = {
            "schema_version": 3,
            "name": "s1",
            "evaluation_status": "evaluated",
        }
        (sd / "study.yaml").write_text(yaml.safe_dump(spec))
        return ws

    def test_force_writes_override_log_for_blocking_finding(self, tmp_path, monkeypatch):
        pytest.importorskip("discovery_engine.report_linter")
        ws = self._ws_with_blocking_finding(tmp_path)
        monkeypatch.setattr(mm, "render_workspace_report", lambda ws_root, **kw: None)

        body, status = mm.render_dashboard(ws, {"force": True})

        assert (body, status) == ({"ok": True}, 200)
        override_path = ws / ".pbg" / "report-lint-overrides.json"
        assert override_path.is_file(), "force=true should log the blocking finding"
        data = json.loads(override_path.read_text())
        checks = {e.get("check") for e in data["overrides"]}
        assert "incomplete_summaries" in checks

    def test_without_force_no_override_log_written(self, tmp_path, monkeypatch):
        ws = self._ws_with_blocking_finding(tmp_path)
        monkeypatch.setattr(mm, "render_workspace_report", lambda ws_root, **kw: None)

        mm.render_dashboard(ws, {})

        assert not (ws / ".pbg" / "report-lint-overrides.json").exists()

    def test_force_idempotent_does_not_double_log(self, tmp_path, monkeypatch):
        pytest.importorskip("discovery_engine.report_linter")
        ws = self._ws_with_blocking_finding(tmp_path)
        monkeypatch.setattr(mm, "render_workspace_report", lambda ws_root, **kw: None)

        mm.render_dashboard(ws, {"force": True})
        mm.render_dashboard(ws, {"force": True})

        data = json.loads((ws / ".pbg" / "report-lint-overrides.json").read_text())
        matching = [e for e in data["overrides"] if e.get("check") == "incomplete_summaries"]
        assert len(matching) == 1

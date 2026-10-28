"""Phase 2.1d — GET /api/report-lint applies `.pbg/report-lint-overrides.json`
server-side, the same way the old `/viva-report` skill/CLI did for Pass B
(`discovery_engine.report.render_workspace_report`'s lint step, via
`report_linter.apply_overrides`).

Before this change the endpoint ran the linter but never consulted the
override file, so a finding downgraded via `--force` still showed up as a
blocking error in the endpoint response — the skill had to re-run/re-derive
the override logic itself to know better. `build_report_lint` now applies the
override file before shaping the response.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib.report_views import build_report_lint

report_linter = pytest.importorskip("discovery_engine.report_linter")


@pytest.fixture
def ws_with_blocking_finding(tmp_path):
    """A study with `evaluation_status: evaluated` and no `conclusion_logic`
    deterministically trips the linter's `incomplete_summaries` error check
    (`field_path="conclusion_logic"`) — no composite build required."""
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


def _write_override_file(ws_root: Path, *, check: str, slug: str, field_path: str) -> str:
    """Write `.pbg/report-lint-overrides.json` with one entry, keyed via the
    SAME `report_linter._override_key` formula the linter itself uses — reused
    here rather than re-derived, matching the production code's contract."""
    key = report_linter._override_key(check=check, slug=slug, field_path=field_path)
    pbg = ws_root / ".pbg"
    pbg.mkdir(parents=True, exist_ok=True)
    (pbg / "report-lint-overrides.json").write_text(json.dumps({
        "schema_version": 1,
        "overrides": [{
            "key": key,
            "added_at": "2026-01-01T00:00:00",
            "reason": "test override",
        }],
    }))
    return key


class TestReportLintAppliesOverrides:
    def test_blocking_finding_present_without_override(self, ws_with_blocking_finding):
        d, code = build_report_lint(ws_with_blocking_finding)
        assert code == 200
        matches = [f for f in d["findings"] if f.get("check") == "incomplete_summaries"]
        assert matches, "fixture should trip incomplete_summaries"
        assert matches[0]["severity"] == "error"

    def test_finding_downgraded_when_overridden(self, ws_with_blocking_finding):
        _write_override_file(
            ws_with_blocking_finding,
            check="incomplete_summaries", slug="s1", field_path="conclusion_logic",
        )
        d, code = build_report_lint(ws_with_blocking_finding)
        assert code == 200
        matches = [f for f in d["findings"] if f.get("check") == "incomplete_summaries"]
        assert matches, "the overridden finding should still be reported (just downgraded)"
        assert matches[0]["severity"] == "warning", (
            "an overridden error-level finding must be downgraded to warning"
        )
        assert matches[0]["message"].startswith("[overridden]")

    def test_unrelated_override_key_does_not_downgrade(self, ws_with_blocking_finding):
        # An override entry for a DIFFERENT (check, slug, field_path) triple
        # must not accidentally downgrade this finding.
        _write_override_file(
            ws_with_blocking_finding,
            check="incomplete_summaries", slug="some-other-study", field_path="conclusion_logic",
        )
        d, code = build_report_lint(ws_with_blocking_finding)
        matches = [f for f in d["findings"] if f.get("check") == "incomplete_summaries"]
        assert matches and matches[0]["severity"] == "error"

    def test_supplemental_dashboard_findings_still_present(self, ws_with_blocking_finding):
        """The dashboard-only supplemental findings (composite resolution,
        readout emit-plan, question/approach, visualization gap) are appended
        after the override-adjusted linter findings — overrides must not drop
        or otherwise disturb them."""
        _write_override_file(
            ws_with_blocking_finding,
            check="incomplete_summaries", slug="s1", field_path="conclusion_logic",
        )
        d, code = build_report_lint(ws_with_blocking_finding)
        assert code == 200
        checks = {f.get("check") for f in d["findings"]}
        # missing_question fires deterministically for a study with no purpose.question.
        assert "missing_question" in checks

    def test_tolerant_of_missing_override_file(self, ws_with_blocking_finding):
        # No override file at all — same as before this change: the finding
        # stays an error, endpoint still 200s.
        d, code = build_report_lint(ws_with_blocking_finding)
        assert code == 200
        matches = [f for f in d["findings"] if f.get("check") == "incomplete_summaries"]
        assert matches[0]["severity"] == "error"

    def test_tolerant_of_malformed_override_file(self, ws_with_blocking_finding):
        pbg = ws_with_blocking_finding / ".pbg"
        pbg.mkdir(parents=True, exist_ok=True)
        (pbg / "report-lint-overrides.json").write_text("{not valid json")
        d, code = build_report_lint(ws_with_blocking_finding)
        assert code == 200
        matches = [f for f in d["findings"] if f.get("check") == "incomplete_summaries"]
        assert matches[0]["severity"] == "error"

"""Thread-A / Task 3 (A3): GET /api/report-lint + per-study readiness panel.

The endpoint runs the existing deterministic linter
(``pbg_superpowers.report_linter.lint_workspace_report``) and returns its
findings as ``{findings: [{study, check, severity, message}]}`` so the
dashboard can surface a per-study readiness panel (SP2b-ii readout-migration +
SP2c band-citation-gap findings). The dashboard adds no AI — it just runs the
linter and renders the result.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
import pytest

_PKG = Path(__file__).parent.parent / "vivarium_dashboard"


@pytest.fixture
def tmp_workspace_with_legacy_readouts(tmp_path, monkeypatch):
    """Workspace with a study that has no readouts → a readout lint finding."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    sd = ws / "studies" / "legacy-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = {
        "schema_version": 3,
        "name": "legacy-study",
        "objective": "test",
        "status": "in_progress",
        "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
        "variants": [],
        "behavior_tests": [{"name": "t1"}],
        # No readouts → triggers the missing_readouts (readout) check.
    }
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_report_lint_endpoint_returns_findings(tmp_workspace_with_legacy_readouts):
    import vivarium_dashboard.server as server
    body, code = server.Handler._report_lint_test(server.WORKSPACE)
    assert code == 200
    findings = json.loads(body)["findings"]
    # The fixture study declares no readouts, so the linter MUST surface
    # findings — no `or findings == []` escape hatch (an empty result here would
    # mean the endpoint silently dropped the linter output). Assert the
    # SP2b-ii readout-migration finding (or an SP2c band-citation-gap) is
    # present, keyed to the study, so the assertion is actually meaningful.
    assert findings, "fixture study with no readouts should yield findings"
    assert any(
        "readout" in f.get("check", "")
        or "band" in f.get("check", "")
        or "needs_human" in f.get("message", "").lower()
        for f in findings
    ), f"expected a readout-migration / band-citation finding; got: {findings}"


def test_report_lint_findings_have_expected_shape(tmp_workspace_with_legacy_readouts):
    import vivarium_dashboard.server as server
    body, code = server.Handler._report_lint_test(server.WORKSPACE)
    assert code == 200
    findings = json.loads(body)["findings"]
    assert findings, "fixture study with no readouts should yield findings"
    f = findings[0]
    for key in ("study", "check", "severity", "message"):
        assert key in f, f"finding missing {key!r}: {f}"


def test_report_lint_tolerant_when_linter_absent(tmp_path, monkeypatch):
    """If the linter import fails, the endpoint returns 200 with empty findings."""
    import vivarium_dashboard.server as server
    body, code = server._report_lint(tmp_path / "does-not-exist")
    assert code == 200
    assert "findings" in json.loads(body)


def test_walkthrough_js_renders_readiness_panel():
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    assert "/api/report-lint" in js
    assert "readiness" in js.lower()
    assert "study-readiness-panel" in js


def test_walkthrough_js_populates_readiness_from_report_render_not_only_domcontentloaded():
    """FIX 1: the investigation report emits its `.study-readiness-panel`
    placeholders asynchronously, so `_populateReadinessPanels` cannot rely on
    the DOMContentLoaded binding alone — it must also be invoked from the
    report-render completion. The self-contained report (no walkthrough.js)
    bakes an exact copy via `.toString()` and invokes it after the study
    sections render. Verify both: the report-build path bakes + invokes it, and
    the DOMContentLoaded binding is still present (study-detail / live-DOM)."""
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    # Report-render path bakes the function via .toString() and invokes it.
    assert "_populateReadinessPanels.toString()" in js, (
        "report-render path should bake _populateReadinessPanels via .toString()"
    )
    build_start = js.index("function _buildInvestigationReportHtml")
    build_region = js[build_start:js.index("function _generateReportHtmlForCurrentIset")] \
        if "function _generateReportHtmlForCurrentIset" in js else js[build_start:]
    assert "_populateReadinessPanels" in build_region, (
        "_populateReadinessPanels must be wired from the report-render path"
    )
    # The original DOMContentLoaded binding is kept for live-DOM contexts.
    assert "addEventListener('DOMContentLoaded', _populateReadinessPanels)" in js
    # Idempotent: no hard one-shot lock; cache lives on the function object so a
    # second call re-keys panels without a duplicate fetch.
    assert "_populateReadinessPanels._cache" in js


def test_study_detail_js_renders_readiness_panel():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    assert "/api/report-lint" in js
    assert "_renderReadinessPanel" in js
    html = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")
    assert 'id="readiness-panel"' in html

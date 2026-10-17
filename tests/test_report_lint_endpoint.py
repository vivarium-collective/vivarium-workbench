"""Thread-A / Task 3 (A3): GET /api/report-lint + per-study readiness panel.

The endpoint runs the existing deterministic linter
(``viva_superpowers.report_linter.lint_workspace_report``) and returns its
findings as ``{findings: [{study, check, severity, message}]}`` so the
dashboard can surface a per-study readiness panel (SP2b-ii readout-migration +
SP2c band-citation-gap findings). The dashboard adds no AI — it just runs the
linter and renders the result.
"""
from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from vivarium_workbench.lib.report_views import build_report_lint

_PKG = Path(__file__).parent.parent / "vivarium_workbench"


@pytest.fixture
def tmp_workspace_with_legacy_readouts(tmp_path):
    """Workspace with a study that has no readouts → a readout lint finding."""
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
    return ws


def test_report_lint_endpoint_returns_findings(tmp_workspace_with_legacy_readouts):
    d, code = build_report_lint(tmp_workspace_with_legacy_readouts)
    assert code == 200
    findings = d["findings"]
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
    d, code = build_report_lint(tmp_workspace_with_legacy_readouts)
    assert code == 200
    findings = d["findings"]
    assert findings, "fixture study with no readouts should yield findings"
    f = findings[0]
    for key in ("study", "check", "severity", "message"):
        assert key in f, f"finding missing {key!r}: {f}"


def test_report_lint_tolerant_when_linter_absent(tmp_path):
    """If the linter import fails, the endpoint returns 200 with empty findings."""
    d, code = build_report_lint(tmp_path / "does-not-exist")
    assert code == 200
    assert "findings" in d


def test_walkthrough_js_renders_readiness_panel():
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    assert "/api/report-lint" in js
    assert "readiness" in js.lower()
    assert "study-readiness-panel" in js


def test_walkthrough_js_populates_readiness_in_live_dom():
    """The live investigation DOM populates its `.study-readiness-panel`
    placeholders via `_populateReadinessPanels`, bound at DOMContentLoaded, with
    a cache on the function object so a re-call re-keys without a duplicate
    fetch. (The old report-render baking path — the report baked an exact copy of
    this function via `.toString()` — is gone now that the investigation report
    is rendered server-side.)"""
    js = (_PKG / "static" / "walkthrough.js").read_text(encoding="utf-8")
    assert "addEventListener('DOMContentLoaded', _populateReadinessPanels)" in js
    # Idempotent: cache lives on the function object.
    assert "_populateReadinessPanels._cache" in js


def test_study_detail_js_renders_readiness_panel():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    assert "/api/report-lint" in js
    # 5-tab layout (Phase 4): the header readiness dropdown became the compact
    # #header-audit-summary link, filled by _renderHeaderAuditSummary.
    assert "_renderHeaderAuditSummary" in js
    html = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")
    assert 'id="header-audit-summary"' in html

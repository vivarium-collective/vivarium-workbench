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
    # surfaces SP2b-ii readout-migration + SP2c band-citation-gap findings
    assert any(
        "readout" in f.get("check", "") or "needs_human" in f.get("message", "").lower()
        for f in findings
    ) or findings == []


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


def test_study_detail_js_renders_readiness_panel():
    js = (_PKG / "static" / "study-detail.js").read_text(encoding="utf-8")
    assert "/api/report-lint" in js
    assert "_renderReadinessPanel" in js
    html = (_PKG / "templates" / "study-detail.html").read_text(encoding="utf-8")
    assert 'id="readiness-panel"' in html

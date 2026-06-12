"""Thread-C / Task 5: golden — the data the spine-C panels need is present on a
REAL study/investigation spec in v2e-invest. READ-ONLY (no writes); skipped when
the workspace is absent.

The "Spine at a glance" panel + the report DAG/acceptance roll-up re-present the
spine's already-computed content. This golden confirms, against real specs, that
``_study_detail_spec`` yields the panel's data sources (a finding for the "Why"
row, a computed_gate_verdict for the "Verdict" row), that ``spine_acceptance``
resolves without error, and that the deterministic report linter (the
"Readiness" source) runs over the real workspace.
"""
from __future__ import annotations

from pathlib import Path

import pytest

V2E = Path("/Users/eranagmon/code/v2e-invest")
pytestmark = pytest.mark.skipif(
    not (V2E / "workspace.yaml").is_file(),
    reason="v2e-invest workspace not present",
)


def _point_at_v2e(monkeypatch):
    import vivarium_dashboard.server as srv
    monkeypatch.setattr(srv, "WORKSPACE", V2E)
    return srv


def _find_study_with_findings(srv):
    """First study dir name whose study.yaml carries findings (read-only scan)."""
    import yaml
    for sy in sorted(V2E.glob("studies/*/study.yaml")):
        try:
            d = yaml.safe_load(sy.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if d.get("findings"):
            return sy.parent.name
    return None


def test_real_study_yields_panel_data_sources(monkeypatch):
    srv = _point_at_v2e(monkeypatch)
    name = _find_study_with_findings(srv)
    if not name:
        pytest.skip("no real study with findings in v2e-invest")
    spec = srv._study_detail_spec(name)
    assert spec is not None
    # "Why" row source — at least one finding with a statement.
    findings = spec.get("findings") or []
    assert findings and any(f.get("statement") for f in findings)
    # "Verdict" row source — computed_gate_verdict is produced (persisted
    # gate_evaluator if present, else the roll_up_verdict fallback).
    cgv = spec.get("computed_gate_verdict") or {}
    assert "result" in cgv
    # "Acceptance" row source — the helper resolves without raising; None is a
    # tolerated absence (the panel omits the row).
    sa = srv._study_acceptance_criterion(name)
    assert sa is None or isinstance(sa, dict)


def test_report_linter_runs_over_real_workspace(monkeypatch):
    """Readiness row source — the deterministic linter runs and returns a
    findings list (possibly empty) without error."""
    srv = _point_at_v2e(monkeypatch)
    body, status = srv._report_lint(V2E)
    assert status == 200
    import json
    data = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body)
    assert "findings" in data and isinstance(data["findings"], list)

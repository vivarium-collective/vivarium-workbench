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


def _find_study_with_findings():
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


def test_real_study_yields_panel_data_sources():
    from vivarium_dashboard.lib.study_spec import load_study_detail_spec
    from vivarium_dashboard.lib.study_enrichment import study_acceptance_criterion
    name = _find_study_with_findings()
    if not name:
        pytest.skip("no real study with findings in v2e-invest")
    spec = load_study_detail_spec(V2E, name)
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
    sa = study_acceptance_criterion(V2E, name)
    assert sa is None or isinstance(sa, dict)


def test_report_linter_runs_over_real_workspace():
    """Readiness row source — the deterministic linter runs and returns a
    findings list (possibly empty) without error."""
    from vivarium_dashboard.lib.report_views import build_report_lint
    data, status = build_report_lint(V2E)
    assert status == 200
    assert "findings" in data and isinstance(data["findings"], list)

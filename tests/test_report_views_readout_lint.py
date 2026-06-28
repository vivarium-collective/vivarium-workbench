# tests/test_report_views_readout_lint.py
from vivarium_dashboard.lib import report_views


def test_emit_plan_findings_flag_orphan_rows(monkeypatch, tmp_path):
    # One study with an authored available readout that isn't an emit leaf.
    def fake_iter(ws):
        return [("demo", None)]
    def fake_build(ws, slug):
        return ({"composite": "ecoli", "rows": [
            {"name": "good", "store_path": "agents.0.listeners.mass.cell_mass",
             "emit_status": "emitted", "annotated": True},
            {"name": "phantom", "store_path": "listeners.nope",
             "emit_status": "not_in_emit_plan", "annotated": True},
            {"name": "derived_ok", "store_path": "",
             "emit_status": "derived", "annotated": True},
        ]}, 200)
    monkeypatch.setattr(report_views, "_iter_study_slugs", fake_iter, raising=False)
    monkeypatch.setattr(report_views._readouts_views, "build_study_readouts", fake_build)

    findings = report_views._readout_emit_plan_findings(tmp_path)
    checks = [(f["study"], f["check"], f["severity"]) for f in findings]
    assert ("demo", "readout-store-path", "error") in checks
    # Only the phantom row produces a finding (emitted + derived are clean).
    assert len(findings) == 1
    assert "phantom" in findings[0]["message"]

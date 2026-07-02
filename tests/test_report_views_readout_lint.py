# tests/test_report_views_readout_lint.py
from vivarium_workbench.lib import report_views


def test_emit_plan_findings_flag_orphan_rows(monkeypatch, tmp_path):
    # One study with an authored available readout that isn't an emit leaf.
    def fake_iter(ws):
        return [("demo", {"readouts": [{"name": "phantom", "status": "available",
                                         "store_path": "listeners.nope"}]})]
    def fake_build(ws, slug):
        return ({"composite": "ecoli", "rows": [
            {"name": "good", "store_path": "agents.0.listeners.mass.cell_mass",
             "emit_status": "emitted", "annotated": True},
            {"name": "phantom", "store_path": "listeners.nope",
             "emit_status": "not_in_emit_plan", "annotated": True},
            {"name": "derived_ok", "store_path": "",
             "emit_status": "derived", "annotated": True},
        ]}, 200)
    monkeypatch.setattr(report_views, "_iter_study_slugs", fake_iter)
    monkeypatch.setattr(report_views._readouts_views, "build_study_readouts", fake_build)

    findings = report_views._readout_emit_plan_findings(tmp_path)
    checks = [(f["study"], f["check"], f["severity"]) for f in findings]
    assert ("demo", "readout-store-path", "error") in checks
    # Only the phantom row produces a finding (emitted + derived are clean).
    assert len(findings) == 1
    assert "phantom" in findings[0]["message"]


def test_build_failure_422_with_note_yields_no_findings(monkeypatch, tmp_path):
    """Fix 1: 422 + non-empty note means composite failed to build → skip per-row lint."""
    def fake_iter(ws):
        return [("broken-study", {"readouts": [{"name": "cm", "status": "available",
                                                 "store_path": "listeners.mass.cell_mass"}]})]
    def fake_build(ws, slug):
        # 422 with a truthy note and all rows showing not_in_emit_plan (empty emit plan).
        return ({"composite": "ecoli",
                 "rows": [{"name": "cm", "store_path": "listeners.mass.cell_mass",
                            "emit_status": "not_in_emit_plan", "annotated": True}],
                 "note": "composite 'ecoli' could not be built — rows unverified: some error"}, 422)
    monkeypatch.setattr(report_views, "_iter_study_slugs", fake_iter)
    monkeypatch.setattr(report_views._readouts_views, "build_study_readouts", fake_build)

    findings = report_views._readout_emit_plan_findings(tmp_path)
    assert findings == [], f"Expected no findings for build-failure study, got: {findings}"


def test_no_authored_readouts_skips_build(monkeypatch, tmp_path):
    """Fix 2: study with no readouts/observables must NOT call build_study_readouts."""
    calls: list = []

    def fake_iter(ws):
        return [("empty-study", {})]  # spec has no readouts key

    def sentinel_build(ws, slug):
        calls.append(slug)
        raise AssertionError("build_study_readouts must not be called for no-readout study")

    monkeypatch.setattr(report_views, "_iter_study_slugs", fake_iter)
    monkeypatch.setattr(report_views._readouts_views, "build_study_readouts", sentinel_build)

    findings = report_views._readout_emit_plan_findings(tmp_path)
    assert findings == []
    assert calls == [], f"build_study_readouts was called unexpectedly: {calls}"

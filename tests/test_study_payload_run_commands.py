"""Task 6: study detail payload exposes run_commands (single source of truth).

Tests that load_study_detail_spec always includes the `run_commands` key whose
`baseline` value matches the canonical CLI string from study_run_commands.
"""
from vivarium_dashboard.lib import study_spec


def test_detail_spec_has_run_commands(fixture_study_ws):
    ws, study = fixture_study_ws
    spec = study_spec.load_study_detail_spec(str(ws), study)
    assert spec["run_commands"]["baseline"] == f"vdash run study {study}"

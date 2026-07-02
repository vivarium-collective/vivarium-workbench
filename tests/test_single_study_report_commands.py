"""Test that the single-study report includes a 'Reproduce this study' block.

Verifies:
- HTML contains the ``reproduce-study`` CSS class.
- Baseline command from ``study_run_commands`` appears verbatim.
- A variant command also appears (fixture has one variant named "var-one").
- The rerun hint appears.
"""
from vivarium_workbench.lib.single_study_report import render_single_study_report


def test_report_has_reproduce_block(fixture_study_ws):
    ws, study = fixture_study_ws
    path = render_single_study_report(str(ws), study)
    html = path.read_text(encoding="utf-8")
    assert "reproduce-study" in html
    assert f"vdash run study {study}" in html


def test_report_reproduce_block_has_variant_command(fixture_study_ws):
    ws, study = fixture_study_ws
    path = render_single_study_report(str(ws), study)
    html = path.read_text(encoding="utf-8")
    # fixture has one variant named "var-one"
    assert f"vdash run study {study} --variant var-one" in html


def test_report_reproduce_block_has_rerun_hint(fixture_study_ws):
    ws, study = fixture_study_ws
    path = render_single_study_report(str(ws), study)
    html = path.read_text(encoding="utf-8")
    assert "vdash rerun" in html

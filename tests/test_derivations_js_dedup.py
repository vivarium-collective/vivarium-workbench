from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SD = (ROOT / "vivarium_workbench/static/study-detail.js")
WT = (ROOT / "vivarium_workbench/static/walkthrough.js")


def test_study_detail_js_reads_derived_not_recompute():
    js = SD.read_text()
    assert "window._study.derived" in js or "_study.derived" in js
    assert "function _deriveConclusionVerdicts" not in js  # copy removed


def test_walkthrough_js_reads_derived_not_recompute():
    js = WT.read_text()
    assert ".derived" in js and "conclusion_verdicts" in js
    assert "function _deriveConclusionVerdicts" not in js  # copy removed

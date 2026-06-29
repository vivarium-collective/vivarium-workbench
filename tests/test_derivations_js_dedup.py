from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SD = (ROOT / "vivarium_dashboard/static/study-detail.js")
WT = (ROOT / "vivarium_dashboard/static/walkthrough.js")


def test_study_detail_js_reads_derived_not_recompute():
    js = SD.read_text()
    assert "window._study.derived" in js or "_study.derived" in js
    assert "function _deriveConclusionVerdicts" not in js  # copy removed

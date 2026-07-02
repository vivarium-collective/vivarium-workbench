from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "static" / "study-detail.js").read_text(encoding="utf-8")


def test_loadteststab_fills_report_card_mounts():
    assert "_fillReportCardModules" in JS
    assert "report-card-mount" in JS
    assert "report_card_urls" in JS
    assert "viz-embed" in JS               # reuses the existing embed class
    # verdict -> pill colour mapping present
    for v in ("within_tol", "drift", "mismatch"):
        assert v in JS

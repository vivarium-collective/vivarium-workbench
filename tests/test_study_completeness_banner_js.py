"""The published investigation page shows a study-completeness banner.

publish.py bakes api/study-completeness/<inv>.json; data-source exposes a
snapshot-only loadStudyCompleteness(); walkthrough renders a warning banner at
the top of the investigation page when findings exist (removed when clean / live).

Static assertions over the source (no browser).
"""
from pathlib import Path

import vivarium_workbench


def _static(name: str) -> str:
    return (Path(vivarium_workbench.__file__).parent / "static" / name).read_text(
        encoding="utf-8"
    )


def test_data_source_loader_is_snapshot_only():
    ds = _static("data-source.js")
    anchor = ds.index("async loadStudyCompleteness(")
    body = ds[anchor:anchor + 400]
    # returns null in live mode (no live endpoint); reads the baked .json in snapshot
    assert 'cfg().mode !== "snapshot"' in body and "return null" in body
    assert '"/api/study-completeness/" + encodeURIComponent(slug) + ".json"' in body


def test_banner_renders_from_baked_findings_and_is_wired():
    js = _static("walkthrough.js")
    assert "function _renderStudyCompletenessBanner(" in js
    # it is invoked when the investigation detail is shown
    assert "_renderStudyCompletenessBanner(name, mountEl)" in js
    anchor = js.index("function _renderStudyCompletenessBanner(")
    body = js[anchor:anchor + 1800]
    # loads via the snapshot-aware DataSource method and only banners when findings exist
    assert "window.DataSource.loadStudyCompleteness(name)" in body
    assert "inv-completeness-banner" in body
    assert "if (!items.length) return;" in body
    # plain-English reason per flagged check
    assert "completed_but_verdict_pending" in body
    assert "declared_composite_unresolved" in body

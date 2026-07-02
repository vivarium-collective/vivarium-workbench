import json
from pathlib import Path
from vivarium_workbench.lib import composite_flush


class _Req:
    steps = 10
    run_id = "r1"
    spec_id = "multiscale_bats.composites.bats_fba.bats_fba"


def test_flush_writes_report_and_empty_analyses(tmp_path, monkeypatch):
    monkeypatch.setattr(composite_flush, "_dispatch_analyses", lambda **kw: [])
    out = composite_flush.run_flush(
        tmp_path, req=_Req(), spec_id=_Req.spec_id,
        db_file=str(tmp_path / "runs.db"), run_id="r1", core=object(),
    )
    assert out["has_report"] is True
    assert out["has_analyses"] is False
    assert json.loads((tmp_path / "analyses.json").read_text()) == []
    html = (tmp_path / "report.html").read_text()
    assert "bats_fba" in html and "10" in html


def test_flush_never_raises(tmp_path, monkeypatch):
    def _boom(**kw):
        raise RuntimeError("analysis exploded")
    monkeypatch.setattr(composite_flush, "_dispatch_analyses", _boom)
    out = composite_flush.run_flush(
        tmp_path, req=_Req(), spec_id=_Req.spec_id,
        db_file=str(tmp_path / "runs.db"), run_id="r1", core=object(),
    )
    assert out["has_analyses"] is False        # swallowed, not raised
    assert (tmp_path / "report.html").is_file()  # report still written

"""Tests for the shared run_declared_results results driver.

Corrected signature (controller ruling, task-3-brief.md's skeleton was
over-parameterized): ``run_declared_results(run_dir, spec, *, ws_root,
run_id, spec_id=None) -> dict``. No ``store``/``sim_data``/``core`` kwargs,
and ``build_analysis_options`` is not precomputed here -- ``run_study_analyses``
resolves analysis scales internally.
"""
import json
from pathlib import Path

from vivarium_workbench.lib import declared_results


def test_empty_spec_is_noop(tmp_path):
    spec = {"analyses": [], "visualizations": [], "baseline": {"composite": "c"}}
    out = declared_results.run_declared_results(
        tmp_path, spec, ws_root=tmp_path, run_id="r1")
    assert out == {"status": "OK", "analyses": None, "report": None,
                    "viz": [], "errors": []}
    assert not (tmp_path / "analyses.json").exists()
    assert not (tmp_path / "report.html").exists()


def test_analyses_error_is_partial_real_run_study_analyses(tmp_path):
    """Exercise the real (unmocked) run_study_analyses.

    With no ``parquet-runs/`` and no ``runs.db`` under run_dir, its own
    "no persistent run store found" branch fires before it would need
    v2ecoli -- so this is real behavior, not a stand-in, and still
    hermetic.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    spec = {"analyses": [{"name": "some_analysis"}], "visualizations": [],
            "baseline": {"composite": "c"}}
    out = declared_results.run_declared_results(
        run_dir, spec, ws_root=tmp_path, run_id="r1")

    assert out["status"] == "PARTIAL"
    assert any("no persistent run store" in e.get("error", "")
               for e in out["errors"])

    # analyses.json must be a JSON LIST (matching the other two writers of
    # this file: composite_flush.run_flush, remote_run_landing.fold_analyses)
    # -- and, since no files were actually produced, an EMPTY list, even
    # though there were errors. The real consumer (composite_run_views.py)
    # derives has_analyses from `content not in ("", "[]")`, so a pure-failure
    # run must read as has_analyses=False, not silently false-positive.
    analyses_path = Path(out["analyses"])
    assert analyses_path == run_dir / "analyses.json"
    raw = analyses_path.read_text()
    assert raw == "[]"
    assert json.loads(raw) == []

    # Report card is still rendered (best-effort) even when analyses fail.
    assert out["report"] == str(run_dir / "report.html")
    assert (run_dir / "report.html").is_file()
    assert out["viz"] == []


def test_analyses_success_writes_nonempty_list(tmp_path, monkeypatch):
    """run_study_analyses producing real output -> analyses.json is a
    non-empty JSON LIST (not the dict shape this module used to write)."""

    def fake_run_study_analyses(study_dir, spec, run_id, ws_root):
        return ["viz/simularium/out.json"], []

    monkeypatch.setattr(declared_results, "run_study_analyses",
                        fake_run_study_analyses)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    spec = {"analyses": [{"name": "some_analysis"}], "visualizations": [],
            "baseline": {"composite": "c"}}
    out = declared_results.run_declared_results(
        run_dir, spec, ws_root=tmp_path, run_id="r1")

    assert out["status"] == "OK"
    analyses_path = Path(out["analyses"])
    entries = json.loads(analyses_path.read_text())
    assert isinstance(entries, list)
    assert len(entries) == 1
    assert entries[0]["written"] == ["viz/simularium/out.json"]
    assert entries[0]["errors"] == []
    assert entries[0]["name"] == "some_analysis"


def test_unregistered_analysis_is_partial_mocked(tmp_path, monkeypatch):
    """Mirrors the brief's original scenario: an unknown analysis name
    surfacing as a per-entry error, this time from run_study_analyses
    (mocked -- real dispatch needs v2ecoli + a parquet run)."""

    def fake_run_study_analyses(study_dir, spec, run_id, ws_root):
        assert study_dir == run_dir
        assert run_id == "r1"
        assert ws_root == tmp_path
        return [], [{"analysis": "nope", "error": "unknown analysis 'nope'"}]

    monkeypatch.setattr(declared_results, "run_study_analyses",
                        fake_run_study_analyses)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    spec = {"analyses": [{"name": "nope"}], "visualizations": [],
            "baseline": {"composite": "c"}}
    out = declared_results.run_declared_results(
        run_dir, spec, ws_root=tmp_path, run_id="r1")

    assert out["status"] == "PARTIAL"
    assert any(e.get("analysis") == "nope" for e in out["errors"])


def test_viz_only_spec_renders_and_status_ok(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    calls = {}

    def fake_render(ws_root, study_dir, spec, spec_id):
        calls["args"] = (ws_root, study_dir, spec, spec_id)
        return (["viz/foo.html"], [])

    monkeypatch.setattr(declared_results, "render_study_visualizations", fake_render)
    spec = {"analyses": [], "visualizations": [{"name": "foo"}],
            "baseline": {"composite": "c"}}
    out = declared_results.run_declared_results(
        run_dir, spec, ws_root=tmp_path, run_id="r1", spec_id="c")

    assert out["status"] == "OK"
    assert out["viz"] == ["viz/foo.html"]
    assert calls["args"] == (tmp_path, run_dir, spec, "c")
    assert out["analyses"] is None
    assert not (run_dir / "analyses.json").exists()
    assert out["report"] == str(run_dir / "report.html")
    assert (run_dir / "report.html").is_file()


def test_viz_errors_also_surface_as_partial(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    def fake_render(ws_root, study_dir, spec, spec_id):
        return ([], [{"error": "render_visualizations failed: boom"}])

    monkeypatch.setattr(declared_results, "render_study_visualizations", fake_render)
    spec = {"analyses": [], "visualizations": [{"name": "foo"}],
            "baseline": {"composite": "c"}}
    out = declared_results.run_declared_results(
        run_dir, spec, ws_root=tmp_path, run_id="r1")

    assert out["status"] == "PARTIAL"
    assert any("boom" in e.get("error", "") for e in out["errors"])


def test_render_report_card_imported_lazily_not_at_module_level():
    """composite_flush must not be imported at declared_results' module
    top-level -- Task 5 will have composite_flush import run_declared_results
    from this module, so a top-level import here would form a load cycle."""
    import vivarium_workbench.lib.declared_results as dr
    assert not hasattr(dr, "render_report_card")
    assert "composite_flush" not in dr.__dict__


def test_report_card_raising_degrades_to_partial(tmp_path, monkeypatch):
    """render_report_card raising must be caught -- the function is
    documented as never raising -- and degrade to PARTIAL, not propagate."""
    import vivarium_workbench.lib.composite_flush as composite_flush

    def boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(composite_flush, "render_report_card", boom)
    monkeypatch.setattr(declared_results, "render_study_visualizations",
                        lambda *a, **k: ([], []))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    spec = {"analyses": [], "visualizations": [{"name": "foo"}],
            "baseline": {"composite": "c"}}

    out = declared_results.run_declared_results(
        run_dir, spec, ws_root=tmp_path, run_id="r1")

    assert out["status"] == "PARTIAL"
    assert any("boom" in e.get("error", "") for e in out["errors"])
    assert out["report"] is None
    assert not (run_dir / "report.html").exists()


def test_report_card_import_failure_degrades_to_partial(tmp_path, monkeypatch):
    """A failure to even IMPORT render_report_card (the lazy import itself)
    must also degrade to PARTIAL, not raise -- this is why the import lives
    inside the try, not just the call."""
    import sys
    import types

    fake_module = types.ModuleType("vivarium_workbench.lib.composite_flush")
    # Deliberately has no render_report_card attribute -> the lazy
    # `from ... import render_report_card` raises ImportError.
    monkeypatch.setitem(sys.modules, "vivarium_workbench.lib.composite_flush",
                        fake_module)
    monkeypatch.setattr(declared_results, "render_study_visualizations",
                        lambda *a, **k: ([], []))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    spec = {"analyses": [], "visualizations": [{"name": "foo"}],
            "baseline": {"composite": "c"}}

    out = declared_results.run_declared_results(
        run_dir, spec, ws_root=tmp_path, run_id="r1")

    assert out["status"] == "PARTIAL"
    assert any("ImportError" in e.get("error", "") for e in out["errors"])
    assert out["report"] is None
    assert not (run_dir / "report.html").exists()

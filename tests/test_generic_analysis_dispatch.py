"""env_worker._run_study_analyses dispatches GENERIC (viva_superpowers
record-based AnalysisStep) analyses over a ResultsHandle, with no v2ecoli.

This is the seam that lets a plain workspace's analysis (e.g. viva-simularium's
SimulariumAnalysis) run in a study's Evaluate-stage flush. Hermetic: a
record-based AnalysisStep is defined here (auto-registers), ResultsHandle is
stubbed so no parquet is needed, and no v2ecoli is installed.
"""
from __future__ import annotations

from pathlib import Path

import discovery_engine.post_sim as post_sim
from discovery_engine.post_sim import AnalysisStep
from vivarium_workbench import env_worker


class _MarkerAnalysis(AnalysisStep):
    """A record-based analysis that writes a file at config['output_path']."""
    name = "test_marker_analysis"
    scale = "single"

    def analyze(self, rows):
        outp = Path(self.config["output_path"] + ".txt")
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(f"rows={len(rows)}", encoding="utf-8")
        return {"n_rows": len(rows), "path": str(outp)}


class _FakeHandle:
    def __init__(self, *a, **k):
        pass

    def records(self, scale=None):
        return [{"time": 0.0}, {"time": 0.5}, {"time": 1.0}]


def _patch(monkeypatch):
    # No real workspace import, and no parquet read.
    monkeypatch.setattr(env_worker, "_workspace", None, raising=False)
    monkeypatch.setattr(env_worker, "_import_workspace_package", lambda *_a, **_k: None)
    monkeypatch.setattr(post_sim, "ResultsHandle", _FakeHandle)


def test_generic_analysis_runs_without_v2ecoli(tmp_path, monkeypatch):
    _patch(monkeypatch)
    res = env_worker._run_study_analyses({
        "entries": [{"name": "test_marker_analysis", "params": {}}],
        "sweep_dir": str(tmp_path),
        "sim_data_path": None,
    })
    assert res["errors"] == [], res["errors"]
    # Wrote into <sweep_dir>/viz/<name>.txt and it was collected.
    out = tmp_path / "viz" / "test_marker_analysis.txt"
    assert out.exists()
    assert out.read_text() == "rows=3"          # saw the ResultsHandle records
    assert any("test_marker_analysis" in w for w in res["written"])


def test_explicit_output_path_honored(tmp_path, monkeypatch):
    _patch(monkeypatch)
    target = tmp_path / "viz" / "custom"
    res = env_worker._run_study_analyses({
        "entries": [{"name": "test_marker_analysis",
                     "params": {"output_path": str(target)}}],
        "sweep_dir": str(tmp_path),
        "sim_data_path": None,
    })
    assert res["errors"] == []
    assert (tmp_path / "viz" / "custom.txt").exists()


def test_unknown_name_routes_to_v2ecoli_path(tmp_path, monkeypatch):
    """A name that is not a generic AnalysisStep falls to the v2ecoli path,
    which (v2ecoli absent) reports its own error — proving the partition, and
    that a generic analysis in the same batch still runs."""
    _patch(monkeypatch)
    res = env_worker._run_study_analyses({
        "entries": [
            {"name": "test_marker_analysis", "params": {}},   # generic
            {"name": "some_v2ecoli_analysis", "params": {}},  # -> v2ecoli path
        ],
        "sweep_dir": str(tmp_path),
        "sim_data_path": None,
    })
    # Generic one still produced its artifact.
    assert (tmp_path / "viz" / "test_marker_analysis.txt").exists()
    # The v2ecoli entry surfaced an error (v2ecoli not installed here).
    assert any("v2ecoli" in str(e.get("error", "")) for e in res["errors"])

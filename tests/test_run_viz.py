"""Unit tests for lib.run_viz — the per-run viz.json writer.

The Runs-tab "📊 Viz" action reads <ws>/.pbg/runs/<run_id>/viz.json; this is
the single primitive both the study-run path and the canonical sims/run.py
runners use to write it.
"""
import json
from pathlib import Path

from vivarium_workbench.lib import run_viz


def _viz_json(ws: Path, run_id: str) -> Path:
    return ws / ".pbg" / "runs" / run_id / "viz.json"


def test_write_run_viz_writes_panels(tmp_path: Path):
    written = run_viz.write_run_viz(
        tmp_path, "run1", {"map": "<div>map</div>", "ts": "<div>ts</div>"})
    assert sorted(written) == ["map", "ts"]
    data = json.loads(_viz_json(tmp_path, "run1").read_text(encoding="utf-8"))
    assert data == {"map": "<div>map</div>", "ts": "<div>ts</div>"}


def test_write_run_viz_skips_render_stubs(tmp_path: Path):
    written = run_viz.write_run_viz(tmp_path, "run2", {
        "good": "<div>ok</div>",
        "broken": "<p>Failed to render visualization</p>",
    })
    assert written == ["good"]
    data = json.loads(_viz_json(tmp_path, "run2").read_text(encoding="utf-8"))
    assert data == {"good": "<div>ok</div>"}


def test_write_run_viz_no_panels_writes_nothing(tmp_path: Path):
    assert run_viz.write_run_viz(tmp_path, "run3", {}) == []
    assert run_viz.write_run_viz(
        tmp_path, "run3", {"x": "Failed to render"}) == []
    # No empty file so build_run_artifact's absent-file fallback still applies.
    assert not _viz_json(tmp_path, "run3").exists()


def test_write_run_viz_non_string_values_skipped(tmp_path: Path):
    written = run_viz.write_run_viz(
        tmp_path, "run4", {"ok": "<div/>", "bad": {"not": "a string"}})
    assert written == ["ok"]


def test_write_run_viz_atomic_leaves_no_tmp(tmp_path: Path):
    run_viz.write_run_viz(tmp_path, "run5", {"a": "<div/>"})
    run_dir = tmp_path / ".pbg" / "runs" / "run5"
    assert not list(run_dir.glob("*.tmp"))


def test_mirror_viz_dir_keys_by_stem(tmp_path: Path):
    viz_dir = tmp_path / "studies" / "s1" / "viz"
    viz_dir.mkdir(parents=True)
    (viz_dir / "spatial_map.html").write_text("<div>map</div>", encoding="utf-8")
    (viz_dir / "richness.html").write_text("<div>rich</div>", encoding="utf-8")
    written = run_viz.mirror_viz_dir(
        tmp_path, "run6",
        [viz_dir / "spatial_map.html", viz_dir / "richness.html"])
    assert sorted(written) == ["richness", "spatial_map"]
    data = json.loads(_viz_json(tmp_path, "run6").read_text(encoding="utf-8"))
    assert data == {"spatial_map": "<div>map</div>", "richness": "<div>rich</div>"}


def test_mirror_viz_dir_skips_missing(tmp_path: Path):
    viz_dir = tmp_path / "viz"
    viz_dir.mkdir()
    (viz_dir / "present.html").write_text("<div/>", encoding="utf-8")
    written = run_viz.mirror_viz_dir(
        tmp_path, "run7",
        [viz_dir / "present.html", viz_dir / "absent.html"])
    assert written == ["present"]

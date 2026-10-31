"""Viz-loop (Task 4, workbench side): clobber fix + panel harvest + labels."""
import shutil
from pathlib import Path

import pytest

from vivarium_workbench.lib import cycle_trace as ct

_FIX = Path(__file__).parent / "_fixtures" / "ws_federation_collision"


def _spec(entries):
    return {"composite": "pkg.c", "visualizations": entries}


def test_build_viz_composite_addressless_raises_vizspecerror():
    from vivarium_workbench.lib.investigations import VizSpecError, build_viz_composite
    with pytest.raises(VizSpecError):
        build_viz_composite({"name": "x"}, {}, {})


def test_render_skips_addressless_and_never_overwrites_existing_figure(tmp_path):
    from vivarium_workbench.lib.investigations import render_visualizations
    inv = tmp_path / "s5"
    (inv / "viz").mkdir(parents=True)
    fig = inv / "viz" / "eigen-catastrophe.html"
    fig.write_text("<p>REAL NATIVE FIGURE</p>")
    out = render_visualizations(_spec([{"name": "eigen-catastrophe"}]), inv, "s5",
                                core_registry={}, build_and_run=lambda d, r: "<p>x</p>")
    assert fig.read_text() == "<p>REAL NATIVE FIGURE</p>"
    assert fig not in out
    assert not (inv / "viz" / "eigen-catastrophe.html.meta.json").exists()


def test_render_exception_never_overwrites_existing_but_stubs_when_absent(tmp_path):
    from vivarium_workbench.lib.investigations import render_visualizations
    inv = tmp_path / "s"
    (inv / "viz").mkdir(parents=True)
    (inv / "viz" / "a.html").write_text("REAL")

    class _V:
        def inputs(self): return {}

    def boom(doc, reg):
        raise RuntimeError("kaboom")
    spec = _spec([{"name": "a", "address": "local:V"}, {"name": "b", "address": "local:V"}])
    render_visualizations(spec, inv, "s", core_registry={"V": _V}, build_and_run=boom)
    assert (inv / "viz" / "a.html").read_text() == "REAL"
    assert "Failed to render" in (inv / "viz" / "b.html").read_text()


def test_successful_class_render_stamps_meta(tmp_path):
    from vivarium_workbench.lib.investigations import render_visualizations
    inv = tmp_path / "s"
    (inv / "viz").mkdir(parents=True)

    class _V:
        def inputs(self): return {}
    spec = _spec([{"name": "ok", "address": "local:V"}])
    out = render_visualizations(spec, inv, "s", core_registry={"V": _V},
                                build_and_run=lambda d, r: "<p>fine</p>")
    assert out
    assert (inv / "viz" / "ok.html.meta.json").is_file()


@pytest.mark.skipif(not ct.engine_available(), reason="engine unavailable")
def test_cycle_frontier_includes_fig_items_with_labels(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(_FIX, ws)
    sy = ws / "studies" / "shared" / "study.yaml"
    sy.write_text(sy.read_text() + "\nvisualizations:\n  - name: eigen\n    rendered: viz/eigen.html\n")
    trace = ct.build_cycle_trace(ws, "local_inv")
    fr = trace["summary"]["frontier"]
    figs = [f for f in fr if str(f["target"]).startswith("fig:")]
    assert figs, [f["target"] for f in fr]
    broken = [f for f in figs if f["kind"] == "viz_broken"]
    assert broken and broken[0]["kind_label"] == "Figure declared but not renderable"
    assert ct._KIND_TAB["viz_broken"] == "visualizations"

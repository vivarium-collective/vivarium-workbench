"""Viz-loop Task 6 (workbench): render_one dispatch, render-viz CLI, Build endpoint,
needs_attention repoint."""
import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from vivarium_workbench.lib import cycle_trace as ct

pytestmark = pytest.mark.skipif(not ct.engine_available(), reason="engine unavailable")

CMD = "python -c \"import pathlib;pathlib.Path('{chart}').parent.mkdir(exist_ok=True);pathlib.Path('{chart}').write_text('<p>fig</p>')\""


def _ws(tmp_path, viz, runs=True):
    ws = tmp_path / "ws"
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (ws / "investigations" / "inv").mkdir(parents=True)
    (ws / "investigations" / "inv" / "investigation.yaml").write_text("name: inv\nstudies: [s1]\n")
    import yaml
    (sd / "study.yaml").write_text(yaml.safe_dump(
        {"name": "s1", "composite": "x.y", "visualizations": viz}))
    if runs:
        c = sqlite3.connect(sd / "runs.db")
        c.execute("CREATE TABLE runs_meta (run_id TEXT, started_at REAL, completed_at REAL, sim_name TEXT, params_json TEXT)")
        c.execute("INSERT INTO runs_meta VALUES ('run-9', 1.0, 2.0, 'default', '{}')")
        c.commit(); c.close()
    return ws, sd


def _meta(p):
    return json.loads(Path(str(p) + ".meta.json").read_text())


def test_render_one_command_renders_and_stamps(tmp_path):
    from vivarium_workbench.lib.viz_build import render_one
    ws, sd = _ws(tmp_path, [{"name": "a", "render": CMD, "chart": "viz/a.html"}])
    r = render_one(ws, "s1", "a")
    assert r["ok"] and r["source_run_id"] == "run-9" and r["chart"] == "viz/a.html"
    assert _meta(sd / "viz/a.html")["source_run_id"] == "run-9"


def test_render_one_class_renders_and_stamps_latest_run(tmp_path):
    from vivarium_workbench.lib.viz_build import render_one

    class _V:
        def inputs(self): return {}
    ws, sd = _ws(tmp_path, [{"name": "c", "address": "local:V"}])
    r = render_one(ws, "s1", "c", core_registry={"V": _V}, inputs_by_class={"V": {}},
                   build_and_run=lambda d, reg: "<p>class fig</p>")
    assert r["ok"], r
    assert (sd / "viz/c.html").read_text() == "<p>class fig</p>"
    assert _meta(sd / "viz/c.html")["source_run_id"] == "run-9"


def test_render_one_static_figure_refused(tmp_path):
    from vivarium_workbench.lib.viz_build import render_one
    ws, sd = _ws(tmp_path, [{"name": "f", "address": "png:charts/f.png"}])
    r = render_one(ws, "s1", "f")
    assert r["ok"] is False and "no refresh recipe" in r["detail"]


def test_render_one_broken_stub_refused(tmp_path):
    from vivarium_workbench.lib.viz_build import render_one
    ws, sd = _ws(tmp_path, [{"name": "b", "rendered": "viz/b.html"}])
    r = render_one(ws, "s1", "b")
    assert r["ok"] is False and "not renderable" in r["detail"]
    assert not (sd / "viz/b.html").exists()


def test_render_viz_cli_writes_meta(tmp_path):
    ws, sd = _ws(tmp_path, [{"name": "a", "render": CMD, "chart": "viz/a.html"}])
    p = subprocess.run([sys.executable, "-m", "vivarium_workbench.cli", "render-viz", "s1",
                        "--viz", "a", "--workspace", str(ws)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr + p.stdout
    assert (sd / "viz/a.html.meta.json").is_file()
    bad = subprocess.run([sys.executable, "-m", "vivarium_workbench.cli", "render-viz", "s1",
                          "--viz", "nope", "--workspace", str(ws)], capture_output=True, text=True)
    assert bad.returncode == 1


def test_build_endpoint_renders_target_and_next_cycle_is_fresh(tmp_path, dashboard_client):
    ws, sd = _ws(tmp_path, [{"name": "a", "render": CMD, "chart": "viz/a.html"}])
    c = dashboard_client(ws)
    t0 = c.get("/api/discovery-cycle/inv").json()
    fr = t0["summary"]["frontier"]
    item = next(f for f in fr if f["target"] == "fig:s1/a")
    assert item["kind"] == "viz_missing" and item["buildable"]
    r = c.post("/api/discovery-cycle/inv/build-viz", json={"target": "fig:s1/a"}).json()
    assert r["ok"], r
    assert (sd / "viz/a.html").is_file() and _meta(sd / "viz/a.html")["source_run_id"] == "run-9"
    t1 = c.get("/api/discovery-cycle/inv").json()
    assert not [f for f in t1["summary"]["frontier"] if f["target"] == "fig:s1/a"]
    bad = c.post("/api/discovery-cycle/inv/build-viz", json={"target": "claim:x"}).json()
    assert bad["ok"] is False


def test_broken_item_declarable_and_declare_endpoint(tmp_path, dashboard_client):
    ws, sd = _ws(tmp_path, [{"name": "b", "rendered": "viz/b.html"}])
    c = dashboard_client(ws)
    item = next(f for f in c.get("/api/discovery-cycle/inv").json()["summary"]["frontier"]
                if f["target"] == "fig:s1/b")
    assert item["kind"] == "viz_broken" and item["declarable"]
    no = c.post("/api/discovery-cycle/inv/declare-viz",
                json={"target": "fig:s1/b", "declare": {"render": CMD}, "rationale": ""}).json()
    assert no["ok"] is False
    ok = c.post("/api/discovery-cycle/inv/declare-viz",
                json={"target": "fig:s1/b", "declare": {"render": CMD}, "rationale": "recipe"}).json()
    assert ok["ok"], ok
    assert "render" in (sd / "study.yaml").read_text()


def test_needs_attention_agrees_with_frontier(tmp_path):
    from vivarium_workbench.lib.needs_attention import (
        open_epistemic_debts, scan_investigation, viz_figure_records)
    ws, sd = _ws(tmp_path, [{"name": "a", "render": CMD, "chart": "viz/a.html"},
                            {"name": "b", "rendered": "viz/b.html"}])
    kinds = {i["ref"]: i["kind"] for i in scan_investigation(ws, "inv")["items"]
             if i["kind"].startswith("viz_")}
    assert kinds == {"fig:s1/a": "viz_missing", "fig:s1/b": "viz_broken"}
    recs = viz_figure_records(ws, "inv", only_study="s1")
    debts = [d for d in open_epistemic_debts({"name": "s1"}, recs) if d["kind"] == "viz-stale"]
    assert {d["ref"] for d in debts} == {"a", "b"}

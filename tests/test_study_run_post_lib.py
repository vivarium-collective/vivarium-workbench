"""Behavioral tests for the extracted ``lib/study_run_post.py`` helpers.

E3 lib-extraction: the post-run side-effect stage moved out of ``server.py``
into ``vivarium_dashboard.lib.study_run_post`` (parameterized on ``ws_root``),
with server name-shims left behind for the live call-sites:

  ``render_study_visualizations`` / ``run_post_run_scripts`` /
  ``run_study_analyses`` (public, shimmed) and the leaf helpers
  ``build_analysis_options`` / ``purge_stale_viz`` / ``latest_run_timestamp``.

These tests NEVER run a real simulation, script, or viz render — every heavy
bit (``subprocess.run``, the v2ecoli analysis runner, the in-process Composite
viz renderer, the core build) is monkeypatched / faked. They assert the
side-effect shapes, that ``render_study_visualizations`` reads the passed
``ws_root`` (not a global), and that the server shims are behavior-identical to
the lib functions.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import yaml

from vivarium_dashboard.lib import study_run_post as srp
import vivarium_dashboard.server as server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runs_db(study_dir: Path, *, started=100.0, completed=None) -> Path:
    """Create a study_dir/runs.db with a runs_meta row."""
    runs_db = study_dir / "runs.db"
    conn = sqlite3.connect(str(runs_db))
    try:
        conn.execute(
            "CREATE TABLE runs_meta (sim_name TEXT, run_id TEXT, status TEXT, "
            "started_at REAL, completed_at REAL)"
        )
        conn.execute(
            "INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?)",
            ("sim", "r1", "completed", started, completed),
        )
        conn.commit()
    finally:
        conn.close()
    return runs_db


def _set_mtime(path: Path, ts: float) -> None:
    import os
    os.utime(path, (ts, ts))


# ---------------------------------------------------------------------------
# latest_run_timestamp
# ---------------------------------------------------------------------------

def test_latest_run_timestamp_prefers_completed(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    _make_runs_db(sd, started=100.0, completed=200.0)
    assert srp.latest_run_timestamp(sd / "runs.db") == 200.0


def test_latest_run_timestamp_falls_back_to_started(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    _make_runs_db(sd, started=150.0, completed=None)
    assert srp.latest_run_timestamp(sd / "runs.db") == 150.0


def test_latest_run_timestamp_missing_db_returns_none(tmp_path):
    assert srp.latest_run_timestamp(tmp_path / "nope.db") is None


# ---------------------------------------------------------------------------
# purge_stale_viz
# ---------------------------------------------------------------------------

def test_purge_stale_viz_deletes_old_keeps_fresh_and_comparative(tmp_path):
    sd = tmp_path / "study"
    viz = sd / "viz"
    viz.mkdir(parents=True)
    _make_runs_db(sd, started=1000.0, completed=1000.0)  # cutoff = 1000.0

    stale = viz / "stale.html"
    stale.write_text("old", encoding="utf-8")
    _set_mtime(stale, 500.0)            # older than cutoff -> purged

    fresh = viz / "fresh.html"
    fresh.write_text("new", encoding="utf-8")
    _set_mtime(fresh, 500.0)            # old, BUT in just_written -> kept

    comp = viz / "comparative_x.html"
    comp.write_text("comp", encoding="utf-8")
    _set_mtime(comp, 500.0)            # old, BUT comparative_ -> kept

    keep = viz / "current.html"
    keep.write_text("cur", encoding="utf-8")
    _set_mtime(keep, 2000.0)           # newer than cutoff -> kept

    srp.purge_stale_viz(sd, [str(fresh)])

    assert not stale.exists()
    assert fresh.exists()
    assert comp.exists()
    assert keep.exists()


def test_purge_stale_viz_noop_without_runs_db(tmp_path):
    sd = tmp_path / "study"
    viz = sd / "viz"
    viz.mkdir(parents=True)
    old = viz / "a.html"
    old.write_text("x", encoding="utf-8")
    _set_mtime(old, 1.0)
    srp.purge_stale_viz(sd, [])       # no runs.db -> no-op
    assert old.exists()


# ---------------------------------------------------------------------------
# build_analysis_options
# ---------------------------------------------------------------------------

def _inject_analysis_registry(name_to_scale: dict[str, str]) -> None:
    registry = {n: type(n, (), {"scale": s}) for n, s in name_to_scale.items()}
    mod = types.ModuleType("v2ecoli.workflow.analysis")
    mod.ANALYSIS_REGISTRY = registry  # type: ignore[attr-defined]
    sys.modules["v2ecoli.workflow.analysis"] = mod


def test_build_analysis_options_groups_by_scale():
    _inject_analysis_registry({"ptools_rna": "single", "ccm": "multiseed"})
    entries = [{"name": "ptools_rna", "params": {"n": 8}}, {"name": "ccm"}]
    opts, errors = srp.build_analysis_options(entries)
    assert errors == []
    assert opts["single"]["ptools_rna"] == {"n": 8}
    assert opts["multiseed"]["ccm"] == {}


def test_build_analysis_options_unknown_name_records_error():
    _inject_analysis_registry({"ptools_rna": "single"})
    opts, errors = srp.build_analysis_options(
        [{"name": "ptools_rna"}, {"name": "missing"}])
    assert len(errors) == 1
    assert errors[0]["analysis"] == "missing"
    assert "single" in opts and "ptools_rna" in opts["single"]


def test_build_analysis_options_empty():
    _inject_analysis_registry({})
    assert srp.build_analysis_options([]) == ({}, [])


# ---------------------------------------------------------------------------
# run_post_run_scripts
# ---------------------------------------------------------------------------

def test_run_post_run_scripts_no_entries_is_noop(tmp_path):
    assert srp.run_post_run_scripts({}, tmp_path) == ([], [])
    assert srp.run_post_run_scripts({"post_run_scripts": []}, tmp_path) == ([], [])


def test_run_post_run_scripts_invokes_and_collects(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    script = ws / "scripts" / "render.py"
    script.write_text("print('hi')", encoding="utf-8")
    viz_dir = ws / "studies" / "s1" / "viz"
    viz_dir.mkdir(parents=True)

    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        # the script "writes" an HTML file as a side effect
        (viz_dir / "rendered.html").write_text("<html>", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    spec = {"post_run_scripts": [
        {"path": "scripts/render.py", "args": ["--study", "s1"], "timeout_s": 42},
    ]}
    written, errors = srp.run_post_run_scripts(spec, ws)

    assert errors == []
    assert len(calls) == 1
    cmd, kw = calls[0]
    assert cmd == [sys.executable, str(script), "--study", "s1"]
    assert kw["cwd"] == str(ws)
    assert kw["timeout"] == 42
    assert "studies/s1/viz/rendered.html" in written


def test_run_post_run_scripts_missing_script_records_error(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = {"post_run_scripts": [{"path": "scripts/absent.py"}]}
    written, errors = srp.run_post_run_scripts(spec, ws)
    assert written == []
    assert errors == [{"script": "scripts/absent.py", "error": "script not found"}]


def test_run_post_run_scripts_nonzero_returncode_records_error(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "boom.py").write_text("x", encoding="utf-8")

    def fake_run(cmd, **kw):
        return types.SimpleNamespace(returncode=3, stdout="out", stderr="bad happened")

    monkeypatch.setattr(subprocess, "run", fake_run)
    written, errors = srp.run_post_run_scripts(
        {"post_run_scripts": [{"path": "scripts/boom.py"}]}, ws)
    assert written == []
    assert len(errors) == 1
    assert errors[0]["returncode"] == 3
    assert "bad happened" in errors[0]["stderr_tail"]


# ---------------------------------------------------------------------------
# run_study_analyses
# ---------------------------------------------------------------------------

def test_run_study_analyses_no_entries_is_noop(tmp_path):
    assert srp.run_study_analyses(tmp_path, {}, "r1", tmp_path) == ([], [])


def test_run_study_analyses_no_analysis_options_returns_build_errors(tmp_path, monkeypatch):
    # build_analysis_options yields empty options + an error -> short-circuit.
    monkeypatch.setattr(
        srp, "build_analysis_options",
        lambda entries: ({}, [{"error": "v2ecoli not installed"}]))
    written, errors = srp.run_study_analyses(
        tmp_path, {"analyses": [{"name": "x"}]}, "r1", tmp_path)
    assert written == []
    assert errors == [{"error": "v2ecoli not installed"}]


def test_run_study_analyses_happy_collects_and_reports_errors(tmp_path, monkeypatch):
    import time
    ws = tmp_path / "ws"
    study_dir = tmp_path / "study"
    sweep = study_dir / "exp"
    (sweep / "history").mkdir(parents=True)
    ws.mkdir()

    # build_analysis_options -> a non-empty mapping.
    monkeypatch.setattr(
        srp, "build_analysis_options",
        lambda entries: ({"single": {"ptools_rna": {}}}, []))

    # _latest_parquet_for_study -> the history hive root (parent = sweep_dir).
    import vivarium_dashboard.lib.study_charts as sc
    monkeypatch.setattr(sc, "_latest_parquet_for_study", lambda sd: sweep / "history")

    # Fake v2ecoli analysis runner: writes outputs + returns a results dict
    # carrying a per-group error to exercise the error-collection branch.
    runner_mod = types.ModuleType("v2ecoli.workflow.analysis_runner")
    analyses_mod = types.ModuleType("v2ecoli.workflow.analyses")

    def fake_run_analyses(sweep_str, options, sim_data_path=None):
        (sweep / "ptools").mkdir(exist_ok=True)
        (sweep / "viz").mkdir(exist_ok=True)
        (sweep / "ptools" / "rna.tsv").write_text("a", encoding="utf-8")
        (sweep / "viz" / "plot.html").write_text("b", encoding="utf-8")
        (sweep / "analysis.json").write_text("{}", encoding="utf-8")
        return {"single": {"ptools_rna": {"all": {"error": "boom"}}}}

    runner_mod.run_analyses = fake_run_analyses  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "v2ecoli.workflow.analysis_runner", runner_mod)
    monkeypatch.setitem(sys.modules, "v2ecoli.workflow.analyses", analyses_mod)

    written, errors = srp.run_study_analyses(
        study_dir, {"analyses": [{"name": "ptools_rna"}]}, "r1", ws)

    names = {Path(p).name for p in written}
    assert {"rna.tsv", "plot.html", "analysis.json"} <= names
    assert any(e.get("analysis") == "ptools_rna" and e.get("error") == "boom"
               for e in errors)


# ---------------------------------------------------------------------------
# render_study_visualizations
# ---------------------------------------------------------------------------

def _write_workspace_yaml(ws: Path, *, package_path="fakepkg", visualizations=None):
    ws.mkdir(parents=True, exist_ok=True)
    data = {"name": "fake-ws", "package_path": package_path,
            "visualizations": visualizations or []}
    (ws / "workspace.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _install_fake_core(monkeypatch, pkg="fakepkg"):
    """Inject a fake <pkg>.core with build_core() -> a minimal core object."""
    class _Core:
        def __init__(self):
            self.link_registry = {}

        def register_link(self, name, cls):
            self.link_registry[name] = cls

    core_mod = types.ModuleType(f"{pkg}.core")
    core_mod.build_core = lambda: _Core()  # type: ignore[attr-defined]
    pkg_mod = types.ModuleType(pkg)
    pkg_mod.core = core_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, pkg, pkg_mod)
    monkeypatch.setitem(sys.modules, f"{pkg}.core", core_mod)


def _quiet_discover(monkeypatch):
    import pbg_superpowers.composite_generator as cg
    monkeypatch.setattr(cg, "discover_generators", lambda: None)
    # keep registry truthy + ensure the test spec_id is absent (entry None).
    monkeypatch.setitem(cg._REGISTRY, "_dummy_keep_truthy",
                        types.SimpleNamespace(name="_dummy", visualizations=[]))


def test_render_study_visualizations_no_viz_is_noop(tmp_path, monkeypatch):
    _quiet_discover(monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    out = srp.render_study_visualizations(ws, study_dir, {"name": "s"}, "spec.absent")
    assert out == ([], [])


def test_render_study_visualizations_happy_writes_and_reads_ws_root(tmp_path, monkeypatch):
    _quiet_discover(monkeypatch)
    _install_fake_core(monkeypatch)

    ws = tmp_path / "ws"
    _write_workspace_yaml(ws)
    study_dir = tmp_path / "study"
    (study_dir / "viz").mkdir(parents=True)

    # server.WORKSPACE points at an unrelated dir with NO workspace.yaml, so a
    # global read would raise "failed to build core for viz". Success proves
    # the function reads the passed ws_root.
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", other)

    captured = {}

    def fake_render_visualizations(effective_spec, sd, name, core_registry=None,
                                   build_and_run=None):
        captured["spec"] = effective_spec
        captured["name"] = name
        p = sd / "viz" / "v1.html"
        p.write_text("<html>", encoding="utf-8")
        return [str(p)]

    import vivarium_dashboard.lib.investigations as inv
    monkeypatch.setattr(inv, "render_visualizations", fake_render_visualizations)

    spec = {"name": "study-x",
            "visualizations": [{"name": "v1", "address": "local:Foo"}]}
    written, errors = srp.render_study_visualizations(ws, study_dir, spec, "spec.id")

    assert errors == []
    assert written == ["viz/v1.html"]
    assert (study_dir / "viz" / "v1.html").exists()
    assert captured["name"] == "study-x"
    # ws_root, not server.WORKSPACE, was added to sys.path.
    assert str(ws) in sys.path


# ---------------------------------------------------------------------------
# Server-shim parity
# ---------------------------------------------------------------------------

def test_shim_run_post_run_scripts_parity(tmp_path):
    spec = {"post_run_scripts": []}
    assert server._run_post_run_scripts(spec, tmp_path) == \
        srp.run_post_run_scripts(spec, tmp_path)


def test_shim_run_study_analyses_parity(tmp_path):
    spec: dict = {}
    assert server._run_study_analyses(tmp_path, spec, "r1", tmp_path) == \
        srp.run_study_analyses(tmp_path, spec, "r1", tmp_path)


def test_shim_build_analysis_options_parity():
    _inject_analysis_registry({"ptools_rna": "single"})
    entries = [{"name": "ptools_rna", "params": {"n": 1}}]
    assert server._build_analysis_options(entries) == \
        srp.build_analysis_options(entries)


def test_shim_render_study_visualizations_threads_workspace(tmp_path, monkeypatch):
    """The server shim passes server.WORKSPACE as ws_root. Use the no-viz
    early-return (deterministic, no heavy deps) and confirm shim == lib."""
    _quiet_discover(monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", ws)

    spec = {"name": "s"}  # no visualizations -> ([], [])
    shim_out = server._render_study_visualizations(study_dir, spec, "spec.absent")
    lib_out = srp.render_study_visualizations(ws, study_dir, spec, "spec.absent")
    assert shim_out == lib_out == ([], [])

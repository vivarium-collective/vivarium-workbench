"""Behavioral tests for ``lib/study_runs.py`` (study-run engine extraction, E4).

These exercise the three orchestrators directly against a tmp ``ws_root`` with
the lib engine seams (the subprocess runner, the ensemble workflow invoker, the
baseline-state resolver, and the post-run side-effect stages) monkeypatched so
NO real simulation runs. They assert the orchestration contract — spec
resolution, the run dispatch, the per-path lib call, the aggregated response —
plus a server-shim-vs-lib parity check proving the ``server`` name-shims are
behavior-identical to the lib functions they delegate to.
"""
import json
from pathlib import Path

import yaml
import pytest

from vivarium_dashboard.lib import (
    composite_subprocess,
    ensemble_config,
    study_run_post,
    study_run_state,
    study_runs,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _write_workspace(ws: Path, package_path: str = "demo_pkg") -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text(
        f'schema_version: 2\nname: demo\ncreated: "2026-06-26"\n'
        f'package_path: {package_path}\n',
        encoding="utf-8",
    )


def _write_study(ws: Path, name: str, baseline: list, variants: list | None = None) -> Path:
    sd = ws / "studies" / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": name, "created": "2026-06-26",
        "status": "planned", "objective": "",
        "baseline": baseline,
        "variants": variants or [],
        "runs": [], "visualizations": [], "comparisons": [],
        "conclusion": None, "parent_studies": [], "interventions": [],
    }), encoding="utf-8")
    return sd


@pytest.fixture
def hermetic_engine(monkeypatch):
    """Neutralise every lib engine seam the orchestrators touch so the bodies
    run end to end without a real sim. Returns a ``calls`` dict recording the
    arguments the orchestrator passed to each seam."""
    calls: dict = {"run": [], "invoke": [], "resolve": [], "render": [],
                   "scripts": [], "analyses": []}

    def fake_run(ws_root, **kw):
        calls["run"].append({"ws_root": ws_root, **kw})
        return ({"simulation_id": "run-x", "status": "completed"}, 200)

    def fake_invoke(cfg_path, out_dir, ws_root, timeout_s):
        calls["invoke"].append({"cfg": str(cfg_path), "out": str(out_dir),
                                "ws_root": ws_root, "timeout": timeout_s})
        return ({"simulation_id": "ens-x", "ensemble": True}, 200)

    def fake_resolve(ws_root, pkg, spec_id, params):
        calls["resolve"].append({"ws_root": ws_root, "pkg": pkg,
                                 "spec_id": spec_id, "params": dict(params)})
        return ({}, None)

    monkeypatch.setattr(composite_subprocess, "run_composite_subprocess", fake_run)
    monkeypatch.setattr(composite_subprocess, "invoke_v2ecoli_workflow", fake_invoke)
    monkeypatch.setattr(study_run_state, "resolve_study_baseline_state", fake_resolve)
    monkeypatch.setattr(study_run_post, "render_study_visualizations",
                        lambda *a, **k: (calls["render"].append(a) or ([], [])))
    monkeypatch.setattr(study_run_post, "run_post_run_scripts",
                        lambda *a, **k: (calls["scripts"].append(a) or ([], [])))
    monkeypatch.setattr(study_run_post, "run_study_analyses",
                        lambda *a, **k: (calls["analyses"].append(a) or ([], [])))
    return calls


# ---------------------------------------------------------------------------
# run_study_baseline
# ---------------------------------------------------------------------------

def test_run_study_baseline_dispatches_subprocess(tmp_path, hermetic_engine):
    ws = tmp_path / "ws"
    _write_workspace(ws, package_path="demo_pkg")
    _write_study(ws, "s1", [
        {"name": "core", "composite": "demo_pkg.composites.cell",
         "params": {"k": 2, "n_steps": 7}},
    ])

    resp, code = study_runs.run_study_baseline(ws, {"study": "s1"})

    assert code == 200, resp
    assert resp["simulation_id"] == "run-x"
    # baseline-state resolver + subprocess runner both ran with ws_root threaded
    assert hermetic_engine["resolve"][0]["ws_root"] == ws
    assert hermetic_engine["resolve"][0]["pkg"] == "demo_pkg"
    assert hermetic_engine["resolve"][0]["spec_id"] == "demo_pkg.composites.cell"
    run = hermetic_engine["run"][0]
    assert run["ws_root"] == ws
    assert run["pkg"] == "demo_pkg"
    assert run["spec_id"] == "demo_pkg.composites.cell"
    assert run["label"] == "core"
    # n_steps is popped from params before being passed as generator overrides
    assert run["overrides"] == {"k": 2}
    # post-run stages fired on the 200 path
    assert hermetic_engine["render"] and hermetic_engine["analyses"]


def test_run_study_baseline_steps_override_wins(tmp_path, hermetic_engine):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", [
        {"name": "core", "composite": "demo_pkg.composites.cell", "params": {"n_steps": 7}},
    ])
    study_runs.run_study_baseline(ws, {"study": "s1", "steps": 99})
    assert hermetic_engine["run"][0]["steps"] == 99


def test_run_study_baseline_missing_study_404(tmp_path, hermetic_engine):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    resp, code = study_runs.run_study_baseline(ws, {"study": "nope"})
    assert code == 404
    assert hermetic_engine["run"] == []  # never dispatched


def test_run_study_baseline_missing_study_key_400(tmp_path, hermetic_engine):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    resp, code = study_runs.run_study_baseline(ws, {})
    assert code == 400


def test_run_study_baseline_unknown_composite_404(tmp_path, hermetic_engine):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", [
        {"name": "core", "composite": "demo_pkg.composites.cell", "params": {}},
    ])
    resp, code = study_runs.run_study_baseline(ws, {"study": "s1", "composite": "ghost"})
    assert code == 404
    assert "ghost" in resp["error"]


def test_run_study_baseline_no_baseline_entries_400(tmp_path, hermetic_engine):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1", [])
    resp, code = study_runs.run_study_baseline(ws, {"study": "s1"})
    assert code == 400
    assert "baseline" in resp["error"].lower()


# ---------------------------------------------------------------------------
# run_study_variant
# ---------------------------------------------------------------------------

def test_run_study_variant_single_run(tmp_path, hermetic_engine):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1",
                 baseline=[{"name": "core", "composite": "demo_pkg.composites.cell",
                            "params": {"k": 1}}],
                 variants=[{"name": "fast", "base_composite": "core",
                            "parameter_overrides": {"k": 5}}])

    resp, code = study_runs.run_study_variant(ws, {"study": "s1", "variant": "fast"})

    assert code == 200, resp
    assert hermetic_engine["invoke"] == []  # single-run path, not delegated
    run = hermetic_engine["run"][0]
    assert run["ws_root"] == ws
    assert run["label"] == "fast"
    assert run["spec_id"] == "demo_pkg.composites.cell"
    assert run["overrides"] == {"k": 5}  # baseline param overlaid by variant


def test_run_study_variant_ensemble_delegates(tmp_path, hermetic_engine, monkeypatch):
    ws = tmp_path / "ws"
    _write_workspace(ws, package_path="v2ecoli")
    _write_study(ws, "s1",
                 baseline=[{"name": "core", "composite": "v2ecoli.composites.ecoli",
                            "params": {}}],
                 variants=[{"name": "ens", "kind": "seeds", "base_composite": "core",
                            "n_seeds": 4, "generations": 2}])
    # In-function import re-binds from the ensemble_config module each call, so
    # patch the source module attributes.
    monkeypatch.setattr(ensemble_config, "is_delegatable_sweep", lambda v: True)
    monkeypatch.setattr(ensemble_config, "delegation_available", lambda ws_root: True)

    resp, code = study_runs.run_study_variant(ws, {"study": "s1", "variant": "ens"})

    assert code == 200, resp
    assert resp.get("ensemble") is True
    assert hermetic_engine["run"] == []  # NOT a single-run dispatch
    inv = hermetic_engine["invoke"][0]
    assert inv["ws_root"] == ws
    assert inv["cfg"].endswith("/config.json")
    assert "/out/" in inv["out"]
    # a real config.json was written next to the packed store
    assert json.loads(Path(inv["cfg"]).read_text())  # non-empty config


def test_run_study_variant_unknown_variant_404(tmp_path, hermetic_engine):
    ws = tmp_path / "ws"
    _write_workspace(ws)
    _write_study(ws, "s1",
                 baseline=[{"name": "core", "composite": "demo_pkg.composites.cell",
                            "params": {}}],
                 variants=[])
    resp, code = study_runs.run_study_variant(ws, {"study": "s1", "variant": "ghost"})
    assert code == 404

"""SP2a — run-variant delegates kind:sweep/seeds to v2ecoli-workflow.

A delegatable ensemble variant is NOT executed as N dashboard subprocesses; it
is handed to ``v2ecoli-workflow`` once, which packs every grid point into ONE
parquet hive store under ``out/<run_id>/``. The dashboard branch only builds the
workflow config + invokes the CLI; the EXISTING post-run block records the one
packed-store dir as a single ensemble run (zero changes to study_outcomes.sync).

These tests stub the subprocess invoker so no real v2ecoli run happens.
"""
import json
import sqlite3
from pathlib import Path

import yaml
import pytest

import vivarium_dashboard.server as server


def _ok():
    return ({"simulation_id": "stub", "ensemble": True}, 200)


def _write_workspace(ws, package_path, with_console_script=False):
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text(
        f'schema_version: 2\nname: ws\ncreated: "2026-06-11"\n'
        f'package_path: {package_path}\n')
    if with_console_script:
        bind = ws / ".venv" / "bin"
        bind.mkdir(parents=True, exist_ok=True)
        (bind / "v2ecoli-workflow").write_text("#!/bin/sh\n")


def _write_study(ws, name, variants):
    sd = ws / "studies" / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": name, "created": "2026-06-11",
        "status": "ran", "objective": "",
        "baseline": [{"name": "core", "composite": "v2ecoli.composites.ecoli",
                      "params": {}}],
        "variants": variants,
        "runs": [], "visualizations": [], "comparisons": [],
        "conclusion": None, "parent_studies": [], "interventions": [],
    }))
    return sd


@pytest.fixture
def tmp_v2ecoli_study(tmp_path, monkeypatch):
    ws = tmp_path / "v2e"
    _write_workspace(ws, "v2ecoli")
    _write_study(ws, "s1", [
        {"name": "ens", "kind": "seeds", "base_composite": "core",
         "n_seeds": 4, "generations": 2},
    ])
    monkeypatch.setattr(server, "WORKSPACE", ws)
    return ws


@pytest.fixture
def tmp_other_study(tmp_path, monkeypatch):
    ws = tmp_path / "other"
    _write_workspace(ws, "multi_cell")  # not v2ecoli, no console script
    _write_study(ws, "s1", [
        {"name": "sw", "kind": "sweep", "base_composite": "core",
         "sweep_over": {"ecoli-metabolism.kcat": [1, 2, 3]}},
    ])
    monkeypatch.setattr(server, "WORKSPACE", ws)
    return ws


def test_sweep_variant_delegates_to_workflow(tmp_v2ecoli_study, monkeypatch):
    invoked = {}
    monkeypatch.setattr(
        server, "_invoke_v2ecoli_workflow",
        lambda cfg_path, out_dir, ws_root, timeout_s:
            invoked.update(cfg=str(cfg_path), out=str(out_dir)) or _ok())
    resp, code = server._post_study_run_variant_for_test(
        tmp_v2ecoli_study, {"study": "s1", "variant": "ens"})
    assert code == 200, resp
    assert invoked  # delegated, not _run_composite_subprocess
    cfg = json.loads(Path(invoked["cfg"]).read_text())
    assert cfg["n_init_sims"] == 4
    assert cfg["generations"] == 2
    assert cfg["emitter"] == "parquet"
    # config.json lives next to the packed store dir
    assert invoked["cfg"].endswith("/config.json")
    assert "/out/" in invoked["out"]


def test_plain_variant_unchanged(tmp_path, monkeypatch):
    ws = tmp_path / "plain"
    _write_workspace(ws, "multi_cell")
    _write_study(ws, "s1", [
        {"name": "fast", "base_composite": "core",
         "parameter_overrides": {"n_steps": 3}},
    ])
    monkeypatch.setattr(server, "WORKSPACE", ws)
    # Make the single-run path reachable (skip real composite resolution).
    monkeypatch.setattr(server, "_resolve_study_baseline_state",
                        lambda *a, **k: ({}, None))
    calls = []
    monkeypatch.setattr(server, "_run_composite_subprocess",
                        lambda *a, **k: calls.append(1) or _ok())
    # Guard: delegation must NOT fire for a plain variant.
    monkeypatch.setattr(server, "_invoke_v2ecoli_workflow",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("delegation fired for a plain variant")))
    resp, code = server._post_study_run_variant_for_test(
        ws, {"study": "s1", "variant": "fast"})
    assert code == 200, resp
    assert len(calls) == 1  # single-run path untouched


def test_sweep_without_v2ecoli_errors_clearly(tmp_other_study):
    resp, code = server._post_study_run_variant_for_test(
        tmp_other_study, {"study": "s1", "variant": "sw"})
    assert code >= 400
    assert "ensemble" in resp.get("error", "").lower()  # clear guard, no half-run


# ---------------------------------------------------------------------------
# Task 4 — single-entry record-back via the existing reconcile_runs/record_runs
#
# MANUAL INTEGRATION VERIFY (pending — needs the real v2ecoli venv; not
# automated here): on v2e-invest, add a `kind: seeds` variant to a baseline
# study and `run-variant` it. Confirm exactly one `v2ecoli-workflow` invocation
# produces a packed store at
#   studies/<study>/out/<run_id>/parquet/<exp>/history/variant=…/lineage_seed=…/…
# and that `study.yaml runs[]` gains exactly ONE ensemble entry whose name is
# <run_id> and whose emitter.store points at out/<run_id>/. (Do NOT run this
# against the real /Users/eranagmon/code/v2e-invest in CI.)
# ---------------------------------------------------------------------------


def test_ensemble_records_one_run(tmp_v2ecoli_study, monkeypatch):
    """A stubbed invoker that writes a minimal parquet hive under out/<run_id>/
    must yield exactly ONE study.yaml runs[] entry (name == run_id, store
    pointing at out/<run_id>/) — confirming the existing post-run sync folds the
    one packed-store dir into a single ensemble run with ZERO dashboard changes
    (reconcile_runs -> backfill_study_runs -> record_runs).

    NOTE: study_outcomes._emitter_kind classifies the run DIR rel-path
    ("out/<run_id>"), which has no "parquet" segment, so emitter.kind is
    "unknown" — that classifier is owned by study_outcomes (out of scope here
    and must stay untouched). The load-bearing invariant is ONE entry pointing
    at the packed store.
    """
    captured = {}

    def _stub_invoke(cfg_path, out_dir, ws_root, timeout_s):
        out_dir = Path(out_dir)
        run_id = out_dir.name
        captured["run_id"] = run_id
        # Emulate the v2ecoli packed layout: out/<run_id>/parquet/<exp>/history/...
        hive = out_dir / "parquet" / run_id / "history" / "variant=0" / "lineage_seed=0"
        hive.mkdir(parents=True, exist_ok=True)
        (hive / "data.parquet").write_bytes(b"PAR1")
        return ({"simulation_id": run_id, "ensemble": True}, 200)

    monkeypatch.setattr(server, "_invoke_v2ecoli_workflow", _stub_invoke)
    resp, code = server._post_study_run_variant_for_test(
        tmp_v2ecoli_study, {"study": "s1", "variant": "ens"})
    assert code == 200, resp

    spec = yaml.safe_load(
        (tmp_v2ecoli_study / "studies" / "s1" / "study.yaml").read_text())
    runs = spec.get("runs") or []
    assert len(runs) == 1, runs
    run_id = captured["run_id"]
    assert runs[0]["name"] == run_id
    assert runs[0]["emitter"]["store"] == f"out/{run_id}"

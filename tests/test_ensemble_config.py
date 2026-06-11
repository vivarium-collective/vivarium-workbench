"""SP2a — variant → v2ecoli-workflow config translator + delegatability check.

A sweep/seeds variant is NOT N independent dashboard subprocesses; it is
DELEGATED to v2ecoli's ``v2ecoli-workflow`` ensemble machinery, which packs all
points into ONE parquet hive store. This module is the pure translator
(dashboard variant shape → workflow-config JSON) plus the guards that decide
whether a variant is delegatable and whether the workspace can delegate.

Grounded against ``v2ecoli/v2ecoli/configs/default.json`` +
``v2ecoli/workflow/variants.py`` — the ``target`` MUST be ``<proc>.<key>``.
"""
import pytest

from vivarium_dashboard.lib.ensemble_config import (
    build_workflow_config,
    delegation_available,
    is_delegatable_sweep,
)


def test_seeds_maps_to_n_init_sims():
    cfg = build_workflow_config(
        variant={"name": "ens", "kind": "seeds", "n_seeds": 5, "generations": 2},
        experiment_id="run-abc", out_dir="/s/out/run-abc")
    assert cfg["n_init_sims"] == 5 and cfg["generations"] == 2
    assert cfg["emitter"] == "parquet"                       # forced
    assert cfg["experiment_id"] == "run-abc"
    assert cfg["out_dir"].endswith("/out/run-abc")


def test_sweep_maps_to_variants_with_proc_key_targets():
    cfg = build_workflow_config(
        variant={"name": "sw", "kind": "sweep",
                 "sweep_over": {"ecoli-metabolism.kcat": [1, 2, 3]}},
        experiment_id="run-x", out_dir="/s/out/run-x")
    v = cfg["variants"]
    assert v["kcat"]["target"] == "ecoli-metabolism.kcat"    # <proc>.<key> preserved
    assert v["kcat"]["value"] == [1, 2, 3]


def test_multi_key_sweep_uses_prod():
    cfg = build_workflow_config(
        variant={"kind": "sweep", "sweep_over": {"a.x": [1, 2], "b.y": [3, 4]}},
        experiment_id="r", out_dir="/o")
    assert cfg["variants"]["op"] == "prod"


def test_sweep_over_bare_key_is_not_delegatable():
    # a bare composite-param name (no "<proc>.") can't be a workflow target
    assert is_delegatable_sweep({"kind": "sweep", "sweep_over": {"b": [1, 2]}}) is False
    assert is_delegatable_sweep({"kind": "sweep", "sweep_over": {"proc.b": [1, 2]}}) is True
    assert is_delegatable_sweep({"kind": "seeds", "n_seeds": 3}) is True
    assert is_delegatable_sweep({"name": "plain"}) is False    # not a sweep at all


# ---------------------------------------------------------------------------
# Task 2 — delegation availability (cheap fs/yaml check; no v2ecoli import)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace_v2ecoli(tmp_path):
    """A workspace whose .venv exposes the v2ecoli-workflow console script."""
    ws = tmp_path / "v2e-ws"
    bind = ws / ".venv" / "bin"
    bind.mkdir(parents=True)
    (bind / "v2ecoli-workflow").write_text("#!/bin/sh\n")
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: v2ecoli\npackage_path: v2ecoli\n")
    return ws


@pytest.fixture
def tmp_workspace_other(tmp_path):
    """A non-v2ecoli workspace: no console script, unrelated package_path."""
    ws = tmp_path / "other-ws"
    (ws / ".venv" / "bin").mkdir(parents=True)
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: viva-munk\npackage_path: multi_cell\n")
    return ws


def test_delegation_available_requires_v2ecoli_workflow(
        tmp_workspace_v2ecoli, tmp_workspace_other):
    assert delegation_available(tmp_workspace_v2ecoli) is True   # console script present
    assert delegation_available(tmp_workspace_other) is False


def test_delegation_unavailable_without_binary_even_if_package_path(tmp_path):
    """Review FIX 2: package_path == v2ecoli alone is NOT enough — the console
    script must exist, else _invoke_v2ecoli_workflow would raise FileNotFoundError.
    """
    ws = tmp_path / "pkg-only"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: v2ecoli\npackage_path: v2ecoli\n")
    assert delegation_available(ws) is False
